"""The FoLT app's Finance module, server half (folt-workflow-ui /finance; BFF package `finance`).

Finance in the app is ERPNext's accounting, reached without the Desk. Nothing here keeps a
ledger, computes a balance or decides a status: every figure is an ERPNext report run as the
person asking, every document is ERPNext's own document saved, submitted, cancelled or moved
along its workflow as that person, and every helper a form needs is ERPNext's own whitelisted
method. What this module adds is the edge:

*Which doctypes and reports.* DOCTYPES and REPORTS are the allow-list. A doctype or report that is
not listed is refused here (and in the BFF in front of it), so a generic "record" endpoint cannot
be pointed at a salary slip, a payroll entry or an employee's bank details. Payroll stays HR's
(hr_portal); Finance sees its accounting entries in the ledger and nothing else of it.

*Who.* Every call requires one of ERPNext's accounting roles (READERS). The Finance staff hold
them alongside FoLT's own roles -- preparers Accounts User, approvers Accounts Manager on top of it, the
Executive Director Auditor -- see seed_test_users.py. Frappe then checks the document or report
permission itself, so the role gate is a floor, never the whole decision.

*Composition.* A record comes back with the things its screen needs in one call -- its ledger
impact, its workflow moves for this person, its timeline, its payments -- and the dashboard is a
handful of native reports run together. Composing them here instead of in the browser keeps the
BFF a pass-through and keeps the arithmetic where it already lives.

Posting is not decided here either: finance_approvals refuses a direct submit of an
approval-governed voucher, and `act` goes through apply_workflow like the Desk's buttons do.
"""

import csv
import io
import re

import frappe
from frappe import _
from frappe.model.workflow import apply_workflow, get_transitions, get_workflow, get_workflow_name
from frappe.utils import cint, flt, getdate, nowdate

from folt_customizations.workflow_access import hold_rejection_reason, is_turn_down

READERS = ("Accounts User", "Accounts Manager", "Auditor", "System Manager")
MANAGERS = ("Accounts Manager", "System Manager")

# doctype -> (group, list columns, searched fields, date field). Columns that a doctype's meta does
# not have on this version are dropped at run time, so a field ERPNext renames is a missing
# column rather than a broken list.
DOCTYPES = {
	# the ledger
	"Journal Entry": ("ledger", ["posting_date", "voucher_type", "title", "total_debit", "cheque_no"], ["name", "title", "user_remark", "cheque_no"], "posting_date"),
	"Account": ("ledger", ["account_name", "account_number", "root_type", "account_type", "account_currency", "is_group", "disabled", "freeze_account"], ["name", "account_name", "account_number"], None),
	"Period Closing Voucher": ("ledger", ["transaction_date", "fiscal_year", "period_start_date", "period_end_date", "closing_account_head"], ["name", "remarks"], "transaction_date"),
	"Exchange Rate Revaluation": ("ledger", ["posting_date", "total_gain_loss"], ["name"], "posting_date"),
	# receivables
	"Sales Invoice": ("receivables", ["posting_date", "customer_name", "due_date", "grand_total", "outstanding_amount", "currency"], ["name", "customer", "customer_name", "po_no"], "posting_date"),
	"Customer": ("receivables", ["customer_name", "customer_group", "territory", "default_currency", "tax_id", "disabled"], ["name", "customer_name", "tax_id", "mobile_no", "email_id"], None),
	# payables
	"Purchase Invoice": ("payables", ["posting_date", "supplier_name", "bill_no", "due_date", "grand_total", "outstanding_amount", "currency"], ["name", "supplier", "supplier_name", "bill_no"], "posting_date"),
	"Supplier": ("payables", ["supplier_name", "supplier_group", "country", "default_currency", "tax_id", "disabled"], ["name", "supplier_name", "tax_id"], None),
	# cash and bank
	"Payment Entry": ("banking", ["posting_date", "payment_type", "party_type", "party_name", "paid_amount", "mode_of_payment", "reference_no"], ["name", "party", "party_name", "reference_no"], "posting_date"),
	"Bank": ("banking", ["bank_name", "swift_number"], ["name", "bank_name"], None),
	"Bank Account": ("banking", ["account_name", "bank", "account", "currency", "is_company_account", "is_default", "disabled"], ["name", "account_name", "bank"], None),
	"Bank Transaction": ("banking", ["date", "bank_account", "description", "deposit", "withdrawal", "unallocated_amount", "reference_number"], ["name", "description", "reference_number", "transaction_id"], "date"),
	"Mode of Payment": ("banking", ["mode_of_payment", "type", "enabled"], ["name"], None),
	# control
	"Budget": ("control", ["budget_against", "cost_center", "project", "account", "budget_amount", "from_fiscal_year", "to_fiscal_year"], ["name", "cost_center", "project", "account"], None),
	"Cost Center": ("control", ["cost_center_name", "cost_center_number", "parent_cost_center", "is_group", "disabled"], ["name", "cost_center_name"], None),
	"Accounting Dimension": ("control", ["label", "document_type", "disabled"], ["name", "label"], None),
	"Finance Book": ("control", ["finance_book_name"], ["name"], None),
	# assets
	"Asset": ("assets", ["asset_name", "asset_category", "location", "custodian", "purchase_date", "total_asset_cost", "value_after_depreciation"], ["name", "asset_name", "item_code"], "purchase_date"),
	"Asset Category": ("assets", ["asset_category_name", "is_cwip_enabled"], ["name"], None),
	"Asset Movement": ("assets", ["transaction_date", "purpose"], ["name"], "transaction_date"),
	"Location": ("assets", ["location_name", "parent_location", "is_group"], ["name", "location_name"], None),
	# tax
	"Sales Taxes and Charges Template": ("tax", ["title", "is_default", "disabled", "tax_category"], ["name", "title"], None),
	"Purchase Taxes and Charges Template": ("tax", ["title", "is_default", "disabled", "tax_category"], ["name", "title"], None),
	"Item Tax Template": ("tax", ["title", "disabled"], ["name", "title"], None),
	"Tax Category": ("tax", ["title", "disabled"], ["name", "title"], None),
	"Tax Rule": ("tax", ["tax_type", "sales_tax_template", "purchase_tax_template", "customer", "supplier", "priority"], ["name"], None),
	"Tax Withholding Category": ("tax", ["category_name"], ["name", "category_name"], None),
	# settings
	"Company": ("settings", ["company_name", "abbr", "default_currency", "country"], ["name", "company_name"], None),
	"Fiscal Year": ("settings", ["year_start_date", "year_end_date", "disabled"], ["name"], "year_start_date"),
	"Accounting Period": ("settings", ["period_name", "start_date", "end_date"], ["name", "period_name"], "start_date"),
	"Payment Terms Template": ("settings", ["template_name"], ["name"], None),
	"Currency Exchange": ("settings", ["date", "from_currency", "to_currency", "exchange_rate"], ["name"], "date"),
	"Accounts Settings": ("settings", [], [], None),
}

# The native reports the module runs. A report that does not exist on this version, or that the
# person's roles do not open, is simply not offered (context).
REPORTS = (
	"General Ledger", "Trial Balance", "Trial Balance for Party", "Balance Sheet", "Profit and Loss Statement",
	"Cash Flow", "Consolidated Financial Statement", "Financial Ratios", "Accounts Receivable",
	"Accounts Receivable Summary", "Accounts Payable", "Accounts Payable Summary", "Customer Ledger Summary",
	"Supplier Ledger Summary", "Payment Ledger", "Sales Register", "Purchase Register", "Item-wise Sales Register",
	"Item-wise Purchase Register", "Gross Profit", "Profitability Analysis", "Budget Variance Report",
	"Bank Reconciliation Statement", "Bank Clearance Summary", "Voucher-wise Balance",
	"Dimension-wise Accounts Balance Report", "Tax Withholding Details", "Fixed Asset Register",
	"Asset Depreciation Ledger", "Asset Depreciations and Balances", "Payment Period Based On Invoice Date",
	"General and Payment Ledger Comparison", "Account Balance",
)

# Link searches into these never answer from Finance: payroll and people's records are HR's.
_HR_ONLY = {
	"Salary Slip", "Salary Structure", "Salary Structure Assignment", "Payroll Entry", "Additional Salary",
	"Employee Tax Exemption Declaration", "Employee Tax Exemption Proof Submission", "Leave Application",
}

# ERPNext's own whitelisted helpers a finance screen calls -- the read-only ones (LOOKUPS) and the
# ones that write (OPERATIONS) are separate allow-lists, so the BFF can treat them differently.
LOOKUPS = {
	"erpnext.accounts.party.get_party_details",
	"erpnext.accounts.party.get_party_account",
	"erpnext.accounts.party.get_due_date",
	"erpnext.stock.get_item_details.get_item_details",
	"erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry",
	"erpnext.accounts.doctype.payment_entry.payment_entry.get_outstanding_reference_documents",
	"erpnext.accounts.doctype.payment_entry.payment_entry.get_party_details",
	"erpnext.accounts.doctype.payment_entry.payment_entry.get_account_details",
	"erpnext.accounts.doctype.payment_entry.payment_entry.get_company_defaults",
	"erpnext.accounts.doctype.payment_entry.payment_entry.get_reference_details",
	"erpnext.accounts.doctype.journal_entry.journal_entry.get_account_details_and_party_type",
	"erpnext.accounts.doctype.journal_entry.journal_entry.get_default_bank_cash_account",
	"erpnext.accounts.doctype.journal_entry.journal_entry.make_reverse_journal_entry",
	"erpnext.accounts.doctype.sales_invoice.sales_invoice.make_sales_return",
	"erpnext.accounts.doctype.purchase_invoice.purchase_invoice.make_debit_note",
	"erpnext.accounts.utils.get_balance_on",
	"erpnext.accounts.utils.get_fiscal_year",
	"erpnext.setup.utils.get_exchange_rate",
	"erpnext.controllers.accounts_controller.get_taxes_and_charges",
	"erpnext.controllers.accounts_controller.get_default_taxes_and_charges",
	"erpnext.controllers.accounts_controller.get_payment_terms",
	"erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool.get_bank_transactions",
	"erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool.get_account_balance",
	"erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool.get_linked_payments",
	"erpnext.assets.doctype.asset.asset.make_asset_movement",
	"erpnext.assets.doctype.asset.asset.make_sales_invoice",
}
OPERATIONS = {
	"erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool.reconcile_vouchers",
	"erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool.auto_reconcile_vouchers",
	"erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool.create_payment_entry_bts",
	"erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool.create_journal_entry_bts",
	"erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool.update_bank_transaction",
	"erpnext.accounts.doctype.bank_transaction.bank_transaction.unclear_reference_payment",
	"erpnext.assets.doctype.asset.depreciation.scrap_asset",
	"erpnext.assets.doctype.asset.depreciation.restore_asset",
	"frappe.core.doctype.communication.email.make",
}

_FILTER_OPS = {"=", "!=", ">", "<", ">=", "<=", "like", "not like", "in", "not in", "between", "is"}
_STD_FIELDS = {"name", "owner", "creation", "modified", "modified_by", "docstatus", "amended_from"}


# --- the edge -------------------------------------------------------------------------------


def _only_finance():
	roles = set(frappe.get_roles())
	if not roles & set(READERS):
		frappe.throw(_("Finance is open to the accounting roles only."), frappe.PermissionError)


def _is_manager() -> bool:
	return bool(set(frappe.get_roles()) & set(MANAGERS))


def _doctype(doctype: str) -> str:
	if doctype not in DOCTYPES:
		frappe.throw(_("{0} is not part of Finance.").format(doctype), frappe.PermissionError)
	return doctype


def _report(report: str) -> str:
	if report not in REPORTS:
		frappe.throw(_("{0} is not one of the Finance reports.").format(report), frappe.PermissionError)
	return report


def _json(value, default=None):
	if value in (None, ""):
		return default
	return frappe.parse_json(value) if isinstance(value, str) else value


# --- context ----------------------------------------------------------------------------------


@frappe.whitelist()
def context():
	"""What every Finance screen needs to know before it asks anything else."""
	_only_finance()
	companies = frappe.get_list(
		"Company",
		fields=["name", "abbr", "default_currency", "country", "default_receivable_account", "default_payable_account",
		        "default_cash_account", "default_bank_account", "cost_center", "default_finance_book"],
		order_by="name",
	)
	default_company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value("Global Defaults", "default_company")
	if companies and default_company not in {c.name for c in companies}:
		default_company = companies[0].name

	fiscal_years = frappe.get_list("Fiscal Year", fields=["name", "year_start_date", "year_end_date", "disabled"], order_by="year_start_date desc")
	current = None
	try:
		from erpnext.accounts.utils import get_fiscal_year

		current = get_fiscal_year(nowdate(), company=default_company, as_dict=True).name
	except Exception:  # noqa: BLE001 -- no fiscal year covers today: say so, don't fail the screen
		current = fiscal_years[0].name if fiscal_years else None

	capabilities = {}
	for doctype in DOCTYPES:
		capabilities[doctype] = {
			ptype: bool(frappe.has_permission(doctype, ptype))
			for ptype in ("read", "create", "write", "submit", "cancel", "delete")
		}

	reports = []
	for name in REPORTS:
		if frappe.db.exists("Report", name) and frappe.get_cached_doc("Report", name).is_permitted():
			reports.append(name)

	roles = set(frappe.get_roles())
	return {
		"companies": companies,
		"default_company": default_company,
		"fiscal_years": fiscal_years,
		"current_fiscal_year": current,
		"dimensions": frappe.get_all("Accounting Dimension", filters={"disabled": 0}, fields=["label", "fieldname", "document_type"]),
		"finance_books": frappe.get_list("Finance Book", pluck="name"),
		"capabilities": capabilities,
		"reports": reports,
		"workflows": {dt: get_workflow_name(dt) for dt in DOCTYPES if get_workflow_name(dt)},
		"roles": {
			"approver": bool(roles & set(MANAGERS)),
			"preparer": bool(roles & {"Accounts User", "Accounts Manager", "System Manager"}),
			"auditor": "Auditor" in roles,
			"administrator": "System Manager" in roles,
		},
		# Statutory integrations are only offered when the app that provides them is installed.
		"integrations": {
			"etims": any(app in frappe.get_installed_apps() for app in ("kenya_compliance", "kenya_compliance_via_slade", "erpnext_kenya")),
		},
	}


# --- lists and records ------------------------------------------------------------------------


def _columns(doctype, wanted):
	meta = frappe.get_meta(doctype)
	out = []
	for fieldname in wanted:
		df = meta.get_field(fieldname)
		if df:
			out.append({"fieldname": fieldname, "label": _(df.label), "fieldtype": df.fieldtype, "options": df.options})
	return out


def _filters(doctype, raw):
	meta = frappe.get_meta(doctype)
	out = []
	for row in _json(raw, []) or []:
		if not isinstance(row, (list, tuple)) or len(row) != 3:
			frappe.throw(_("A filter is [field, operator, value]."))
		field, op, value = row
		if op not in _FILTER_OPS or (field not in _STD_FIELDS and not meta.get_field(field)):
			frappe.throw(_("Cannot filter {0} by {1} {2}.").format(doctype, field, op))
		out.append([doctype, field, op, value])
	return out


@frappe.whitelist()
def records(doctype, q=None, filters=None, order_by=None, start=0, page_length=20):
	"""One page of a Finance list, as the person may see it, with its total."""
	_only_finance()
	_doctype(doctype)
	meta = frappe.get_meta(doctype)
	if meta.issingle:
		frappe.throw(_("{0} is a single settings record, not a list.").format(doctype))
	_group, wanted, search, _date = DOCTYPES[doctype]
	columns = _columns(doctype, wanted)
	fields = ["name", "docstatus", "modified"] + [c["fieldname"] for c in columns]
	for extra in ("status", "workflow_state", "company", meta.title_field):
		if extra and meta.get_field(extra) and extra not in fields:
			fields.append(extra)

	where = _filters(doctype, filters)
	or_filters = [[doctype, f, "like", f"%{q}%"] for f in search if f == "name" or meta.get_field(f)] if q else None

	order = "modified desc"
	if order_by:
		field, _sp, direction = str(order_by).partition(" ")
		if (field in _STD_FIELDS or meta.get_field(field)) and direction.lower() in ("", "asc", "desc"):
			order = f"{field} {direction.lower() or 'asc'}"

	page_length = min(max(cint(page_length) or 20, 1), 500)
	rows = frappe.get_list(doctype, fields=fields, filters=where, or_filters=or_filters, order_by=order,
	                       limit_start=cint(start), limit_page_length=page_length)
	total = frappe.get_list(doctype, fields=[{"COUNT": "*", "as": "total"}], filters=where, or_filters=or_filters)
	return {
		"columns": columns,
		"rows": rows,
		"total": total[0].total if total else 0,
		"title_field": meta.title_field,
		"submittable": bool(meta.is_submittable),
	}


def _mask(value):
	value = str(value or "")
	return re.sub(r"[0-9A-Za-z](?=.{4})", "•", value) if value else value


@frappe.whitelist()
def record(doctype, name=None):
	"""A document with everything its screen shows: ledger impact, workflow moves, timeline."""
	_only_finance()
	_doctype(doctype)
	meta = frappe.get_meta(doctype)
	name = doctype if meta.issingle else name
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	doc.apply_fieldlevel_read_permissions()

	from frappe.desk.form.load import get_docinfo

	get_docinfo(doc)
	docinfo = frappe.response.pop("docinfo", {}) or {}

	data = doc.as_dict(convert_dates_to_str=True)
	if doctype == "Bank Account" and not _is_manager():
		# Account numbers are shown in full to the people who manage them, and as their last four
		# characters to everybody else who works with the account.
		for field in ("bank_account_no", "iban"):
			data[field] = _mask(data.get(field))

	workflow = None
	workflow_name = get_workflow_name(doctype)
	if workflow_name:
		wf = get_workflow(doctype)
		state = doc.get(wf.workflow_state_field)
		moves = []
		if doc.docstatus < 2:
			for t in get_transitions(doc):
				moves.append({"action": t.action, "next_state": t.next_state, "needs_reason": is_turn_down(wf, state, t.next_state)})
		workflow = {"name": workflow_name, "state": state, "transitions": moves}

	gl_entries = []
	if meta.is_submittable and doc.docstatus > 0 and frappe.has_permission("GL Entry", "read"):
		gl_entries = frappe.get_list(
			"GL Entry",
			filters={"voucher_type": doctype, "voucher_no": doc.name},
			fields=["name", "posting_date", "account", "party_type", "party", "debit", "credit", "account_currency",
			        "debit_in_account_currency", "credit_in_account_currency", "against", "cost_center", "project",
			        "is_cancelled", "remarks"],
			order_by="creation asc, name asc",
		)

	payments = []
	if doctype in ("Sales Invoice", "Purchase Invoice") and doc.docstatus == 1 and frappe.has_permission("Payment Ledger Entry", "read"):
		payments = frappe.get_list(
			"Payment Ledger Entry",
			filters={"against_voucher_type": doctype, "against_voucher_no": doc.name, "delinked": 0, "voucher_no": ["!=", doc.name]},
			fields=["voucher_type", "voucher_no", "posting_date", "amount", "account_currency"],
			order_by="posting_date asc",
		)

	perms = docinfo.get("permissions") or {}
	return {
		"doctype": doctype,
		"name": doc.name,
		"doc": data,
		"permissions": {k: bool(perms.get(k)) for k in ("read", "write", "create", "submit", "cancel", "amend", "delete", "print", "email")},
		"workflow": workflow,
		"gl_entries": gl_entries,
		"payments": payments,
		"timeline": {
			"versions": [{"owner": v.get("owner"), "creation": v.get("creation"), "data": v.get("data")} for v in docinfo.get("versions", [])],
			"comments": [{"comment_type": c.get("comment_type"), "content": c.get("content"), "owner": c.get("owner"), "creation": c.get("creation")} for c in docinfo.get("comments", [])],
			"workflow_logs": [{"content": c.get("content"), "owner": c.get("owner"), "creation": c.get("creation")} for c in docinfo.get("workflow_logs", [])],
			"communications": [{"subject": c.get("subject"), "recipients": c.get("recipients"), "sender": c.get("sender"), "creation": c.get("creation"), "communication_type": c.get("communication_type")} for c in docinfo.get("communications", [])],
		},
		"attachments": [{"name": a.get("name"), "file_name": a.get("file_name"), "file_url": a.get("file_url"), "is_private": a.get("is_private")} for a in docinfo.get("attachments", [])],
		"print_formats": [frappe.get_meta(doctype).default_print_format or "Standard"] + frappe.get_all(
			"Print Format", filters={"doc_type": doctype, "disabled": 0}, pluck="name", order_by="name"
		),
	}


@frappe.whitelist()
def meta(doctype):
	"""The fields of a Finance doctype (and its child tables), for a form drawn from them."""
	_only_finance()
	_doctype(doctype)

	def fields_of(m):
		return [
			{k: df.get(k) for k in ("fieldname", "label", "fieldtype", "options", "reqd", "read_only", "hidden", "default",
			                        "depends_on", "mandatory_depends_on", "read_only_depends_on", "in_list_view", "columns",
			                        "description", "allow_on_submit", "precision", "non_negative", "collapsible", "fetch_from",
			                        "permlevel", "bold", "length")}
			for df in m.fields
		]

	m = frappe.get_meta(doctype)
	children = {}
	for df in m.get_table_fields():
		children[df.options] = fields_of(frappe.get_meta(df.options))
	return {
		"doctype": doctype,
		"issingle": bool(m.issingle),
		"is_submittable": bool(m.is_submittable),
		"is_tree": bool(m.is_tree),
		"title_field": m.title_field,
		"autoname": m.autoname,
		"fields": fields_of(m),
		"children": children,
		"writable_permlevels": m.get_permlevel_access("write"),
	}


# --- writes -----------------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def save(doctype, doc, name=None):
	"""Save a draft (new or existing), or a single settings record. Never submits.

	For an existing document only the fields sent are changed, and a child table sent is the whole
	table. `modified` travels with an existing document, so somebody else's newer save is refused
	rather than overwritten.
	"""
	_only_finance()
	_doctype(doctype)
	values = _json(doc, {}) or {}
	for field in ("docstatus", "doctype", "amended_from", "workflow_state", "folt_rejection_reason"):
		values.pop(field, None)
	m = frappe.get_meta(doctype)

	if m.issingle:
		target = frappe.get_doc(doctype)
		target.update(values)
		target.save()
	elif name:
		target = frappe.get_doc(doctype, name)
		if target.docstatus == 2:
			frappe.throw(_("{0} is cancelled. Amend it to make a new version.").format(name))
		target.update(values)
		_template_taxes(target, m)
		target.save()
	else:
		values.pop("name", None)
		target = frappe.get_doc({"doctype": doctype, **values})
		_template_taxes(target, m)
		target.insert()
	return {"name": target.name}


def _template_taxes(doc, m):
	"""A tax template chosen with no tax rows gets the template's rows, as the Desk's form does.

	ERPNext's controllers do not copy a template's rows on save; the Desk does it in the browser
	when the template is picked. Doing it here keeps an invoice saved from the app from quietly
	going out without its VAT.
	"""
	df = m.get_field("taxes_and_charges")
	if not df or not doc.get("taxes_and_charges") or doc.get("taxes"):
		return
	from erpnext.controllers.accounts_controller import get_taxes_and_charges

	for row in get_taxes_and_charges(df.options, doc.taxes_and_charges) or []:
		doc.append("taxes", row)


# The setters each controller's own validate() runs, in its order, minus the checks: what turns a
# typed draft into the figures ERPNext would save.
_PREVIEW = {
	"Journal Entry": ("set_exchange_rate", "set_amounts_in_company_currency", "set_total_debit_credit"),
	"Sales Invoice": ("set_missing_values", "calculate_taxes_and_totals", "set_total_in_words"),
	"Purchase Invoice": ("set_missing_values", "calculate_taxes_and_totals", "set_total_in_words"),
	"Payment Entry": ("setup_party_account_field", "set_missing_values", "set_liability_account", "set_missing_ref_details",
	                  "set_exchange_rate", "set_amounts", "apply_taxes", "set_amounts_after_tax", "set_title", "set_total_in_words"),
}


@frappe.whitelist(methods=["POST"])
def preview(doctype, doc):
	"""ERPNext's own defaults and totals for an unsaved draft. Writes nothing.

	The form shows what ERPNext would make of the document -- item details, accounts, taxes from
	the template, totals, outstanding, due date -- by running the controller's own methods on an
	unsaved copy, so a total in the form is ERPNext's total, not one the browser added up.
	"""
	_only_finance()
	_doctype(doctype)
	if not (frappe.has_permission(doctype, "create") or frappe.has_permission(doctype, "write")):
		frappe.throw(_("You cannot prepare a {0}.").format(_(doctype)), frappe.PermissionError)
	values = _json(doc, {}) or {}
	values.pop("docstatus", None)
	draft = frappe.get_doc({"doctype": doctype, **values})
	draft.docstatus = 0
	for method in _PREVIEW.get(doctype, ("set_missing_values",)):
		fn = getattr(draft, method, None)
		if not callable(fn):
			continue
		try:
			fn()
		except Exception:  # noqa: BLE001
			# A half-filled form is normal while somebody types -- a party not chosen yet, an item
			# half typed -- and the controllers do not all raise ValidationError for it. The
			# preview is advice; the save validates for real and says what is missing.
			frappe.clear_last_message()
	return draft.as_dict(convert_dates_to_str=True)


@frappe.whitelist(methods=["POST"])
def act(doctype, name, action, transition=None, reason=None):
	"""Move a document: a workflow step, or ERPNext's submit / cancel / amend / delete.

	A workflow step goes through apply_workflow exactly as the Desk's button does, with the reason
	for a turn-down parked first (workflow_access). Submit and cancel are ERPNext's own; on an
	approval-governed voucher they are refused by finance_approvals, so the screen offers the
	workflow's moves instead.
	"""
	_only_finance()
	_doctype(doctype)
	doc = frappe.get_doc(doctype, name)

	if action == "transition":
		if not transition:
			frappe.throw(_("Which step?"))
		if reason and reason.strip():
			hold_rejection_reason(doctype, name, reason.strip())
		apply_workflow(doc, transition)
		return {"name": doc.name}
	if action in ("submit", "cancel") and get_workflow_name(doctype):
		# Not only finance_approvals' request check: a document with a workflow is posted and
		# reversed by its workflow's moves, whoever and wherever the call comes from.
		frappe.throw(
			_("{0} is posted and reversed through its approval workflow. Use the workflow's steps.").format(_(doctype)),
			frappe.PermissionError,
			title=_("Needs approval"),
		)
	if action == "submit":
		doc.submit()
		return {"name": doc.name}
	if action == "cancel":
		doc.cancel()
		return {"name": doc.name}
	if action == "amend":
		if doc.docstatus != 2:
			frappe.throw(_("Only a cancelled document can be amended."))
		if not frappe.has_permission(doctype, "amend", doc):
			frappe.throw(_("You cannot amend {0}.").format(name), frappe.PermissionError)
		new = frappe.copy_doc(doc)
		new.amended_from = doc.name
		new.docstatus = 0
		new.insert()
		return {"name": new.name}
	if action == "delete":
		frappe.delete_doc(doctype, name)
		return {"name": None}
	frappe.throw(_("Unknown action {0}.").format(action))


@frappe.whitelist(methods=["POST"])
def attach(doctype, docname):
	"""Frappe's own upload_file, private, onto a Finance document; Frappe checks the write right."""
	_only_finance()
	_doctype(doctype)
	frappe.form_dict.update({"doctype": doctype, "docname": docname, "is_private": 1})
	from frappe.handler import upload_file

	f = upload_file()
	return {"name": f.name, "file_name": f.file_name, "file_url": f.file_url, "is_private": f.is_private}


@frappe.whitelist()
def copy(doctype, name):
	"""An unsaved copy of a document, to start a new one from (Duplicate). Writes nothing."""
	_only_finance()
	_doctype(doctype)
	source = frappe.get_doc(doctype, name)
	source.check_permission("read")
	new = frappe.copy_doc(source)
	new.docstatus = 0
	return new.as_dict(convert_dates_to_str=True)


# --- searches and helpers ---------------------------------------------------------------------


@frappe.whitelist()
def link_search(doctype, txt="", filters=None, page_length=20):
	"""Frappe's own link search, minus the doctypes that are HR's alone."""
	_only_finance()
	if doctype in _HR_ONLY:
		frappe.throw(_("{0} belongs to HR.").format(doctype), frappe.PermissionError)
	from frappe.desk.search import search_link

	return search_link(doctype, txt or "", filters=_json(filters), page_length=min(cint(page_length) or 20, 50))


def _call(method, args, allowed):
	_only_finance()
	if method not in allowed:
		frappe.throw(_("{0} is not available from Finance.").format(method), frappe.PermissionError)
	return frappe.call(method, **(_json(args, {}) or {}))


@frappe.whitelist(methods=["POST"])
def lookup(method, args=None):
	"""One of ERPNext's read-only helpers a finance form uses (LOOKUPS)."""
	return _call(method, args, LOOKUPS)


@frappe.whitelist(methods=["POST"])
def operate(method, args=None):
	"""One of ERPNext's writing helpers a finance screen uses (OPERATIONS)."""
	return _call(method, args, OPERATIONS)


@frappe.whitelist(methods=["POST"])
def payment_reconciliation(action, doc, args=None):
	"""ERPNext's Payment Reconciliation tool, step by step, on an unsaved copy of the tool.

	get_unreconciled_entries fills the invoices and payments, allocate_entries proposes the
	matches, reconcile books them. The tool is a single doctype whose state lives in the form, so
	the screen sends the whole tool back each time, exactly as the Desk form does.
	"""
	_only_finance()
	if action not in ("get_unreconciled_entries", "allocate_entries", "reconcile"):
		frappe.throw(_("Unknown step {0}.").format(action))
	if not frappe.has_permission("Payment Reconciliation", "write"):
		frappe.throw(_("You cannot reconcile payments."), frappe.PermissionError)
	tool = frappe.get_doc({**(_json(doc, {}) or {}), "doctype": "Payment Reconciliation"})
	if action == "allocate_entries":
		tool.allocate_entries(_json(args, {}))
	else:
		getattr(tool, action)()
	return tool.as_dict(convert_dates_to_str=True)


# --- reports ----------------------------------------------------------------------------------


def _run(report, filters):
	from frappe.desk.query_report import run

	return run(_report(report), filters=_json(filters, {}) or {}, ignore_prepared_report=True)


@frappe.whitelist(methods=["POST"])
def run_report(report, filters=None):
	"""A native ERPNext report, run as the person asking, exactly as the Desk runs it."""
	_only_finance()
	out = _run(report, filters)
	return {k: out.get(k) for k in ("columns", "result", "message", "chart", "report_summary", "add_total_row", "skip_total_row")}


def _cell(value):
	if isinstance(value, str) and len(value) > 1 and value[0] == value[-1] == "'":
		return value[1:-1]  # the reports quote their label rows ('Total', 'Opening')
	return value


@frappe.whitelist()
def export_report(report, filters=None, file_format="xlsx"):
	"""The same report as a spreadsheet: the report's own rows and columns, nothing recomputed."""
	_only_finance()
	out = _run(report, filters)
	columns = [c if isinstance(c, dict) else {"label": c, "fieldname": c} for c in out.get("columns") or []]
	columns = [c for c in columns if not c.get("hidden")]
	table = [[_(c.get("label") or c.get("fieldname")) for c in columns]]
	for row in out.get("result") or []:
		if isinstance(row, dict):
			indent = "    " * cint(row.get("indent") or 0)
			cells = [_cell(row.get(c.get("fieldname"))) for c in columns]
			if cells and isinstance(cells[0], str):
				cells[0] = indent + cells[0]
		else:
			cells = [_cell(v) for v in row[: len(columns)]]
		table.append(["" if v is None else v for v in cells])

	filename = f"{report} {nowdate()}"
	if file_format == "csv":
		buf = io.StringIO()
		csv.writer(buf).writerows(table)
		frappe.response.filename = f"{filename}.csv"
		frappe.response.filecontent = buf.getvalue()
	else:
		from frappe.utils.xlsxutils import make_xlsx

		frappe.response.filename = f"{filename}.xlsx"
		frappe.response.filecontent = make_xlsx(table, report[:31]).getvalue()
	frappe.response.type = "binary"


# --- trees ------------------------------------------------------------------------------------


@frappe.whitelist()
def account_tree(company):
	"""The chart of accounts in tree order with ERPNext's own rolled-up balances."""
	_only_finance()
	accounts = frappe.get_list(
		"Account",
		filters={"company": company},
		fields=["name", "account_name", "account_number", "parent_account", "is_group", "root_type", "report_type",
		        "account_type", "account_currency", "disabled", "freeze_account", "lft", "rgt"],
		order_by="lft",
		limit_page_length=0,
	)
	balances = {}
	if frappe.has_permission("GL Entry", "read"):
		from erpnext.accounts.utils import get_account_balances_coa

		for row in get_account_balances_coa(company):
			balances[row.get("value")] = row
	for a in accounts:
		b = balances.get(a.name) or {}
		a.balance = b.get("balance")
		a.balance_in_account_currency = b.get("balance_in_account_currency")
	return {"currency": frappe.get_cached_value("Company", company, "default_currency"), "accounts": accounts}


@frappe.whitelist()
def cost_center_tree(company):
	_only_finance()
	return frappe.get_list(
		"Cost Center",
		filters={"company": company},
		fields=["name", "cost_center_name", "cost_center_number", "parent_cost_center", "is_group", "disabled", "lft"],
		order_by="lft",
		limit_page_length=0,
	)


# --- the dashboard ----------------------------------------------------------------------------


def _summary(out, label):
	for row in out.get("report_summary") or []:
		if row.get("label") == label:
			return flt(row.get("value"))
	return None


def _quiet(report, filters, unavailable=None):
	"""A report the reader may not open, or one that fails, is a missing section named in
	`unavailable` -- not a failed dashboard."""
	try:
		return _run(report, filters)
	except Exception as e:  # noqa: BLE001
		frappe.clear_last_message()
		if unavailable is not None:
			unavailable.append({"report": report, "reason": "permission" if isinstance(e, frappe.PermissionError) else "error"})
		if not isinstance(e, frappe.PermissionError):
			frappe.log_error(f"Finance dashboard: {report}")
		return None


@frappe.whitelist()
def dashboard(company, from_date, to_date, cost_center=None, project=None, finance_book=None):
	"""The finance dashboard: native reports run together for one company and period.

	Each figure names the report it came from; the browser adds nothing up. Cost centre and
	project narrow the profit and loss figures (the reports that accept them); the balance sheet
	figures are the company's.
	"""
	_only_finance()
	from_date, to_date = str(getdate(from_date)), str(getdate(to_date))
	period = {"company": company, "filter_based_on": "Date Range", "period_start_date": from_date, "period_end_date": to_date,
	          "from_fiscal_year": None, "to_fiscal_year": None, "finance_book": finance_book, "presentation_currency": None}
	narrow = {}
	if cost_center:
		narrow["cost_center"] = [cost_center]
	if project:
		narrow["project"] = [project]

	unavailable = []
	fy = None
	try:
		from erpnext.accounts.utils import get_fiscal_year

		fy = get_fiscal_year(to_date, company=company, as_dict=True).name
	except Exception:  # noqa: BLE001
		frappe.clear_last_message()
	pl = _quiet("Profit and Loss Statement", {**period, **narrow, "periodicity": "Monthly", "accumulated_values": 0}, unavailable)
	bs = _quiet("Balance Sheet", {**period, "periodicity": "Yearly", "accumulated_values": 1}, unavailable)
	cf = _quiet("Cash Flow", {**period, "periodicity": "Monthly", "accumulated_values": 0}, unavailable)
	ageing = {"company": company, "report_date": to_date, "ageing_based_on": "Due Date", "range": "30, 60, 90, 120"}
	ar = _quiet("Accounts Receivable Summary", ageing)
	ap = _quiet("Accounts Payable Summary", ageing)
	by_cc = _quiet("Profitability Analysis", {"company": company, "based_on": "Cost Center", "from_date": from_date, "to_date": to_date,
	                                          "fiscal_year": fy, "finance_book": finance_book}, unavailable) if fy else None
	budget = _quiet("Budget Variance Report", {"company": company, "from_fiscal_year": fy, "to_fiscal_year": fy, "period": "Yearly",
	                                           "budget_against": "Cost Center"}, unavailable) if fy else None

	def tree_rows(out, depth):
		return [
			{"account": r.get("account"), "label": r.get("account_name"), "total": flt(r.get("total")), "is_group": cint(r.get("is_group"))}
			for r in (out or {}).get("result") or []
			if isinstance(r, dict) and r.get("parent_account") and cint(r.get("indent")) == depth and flt(r.get("total"))
		]

	def party_rows(out):
		if not out:
			return None
		rows = [r for r in out.get("result") or [] if isinstance(r, dict) and r.get("party")]
		keys = ("outstanding", "range0", "range1", "range2", "range3", "range4", "range5")
		totals = {k: sum(flt(r.get(k)) for r in rows) for k in keys}
		# The ageing buckets as the report names them ("0-30", "31-60"…, "Not Due" where it has one).
		labels = {c.get("fieldname"): _(c.get("label")) for c in out.get("columns") or [] if isinstance(c, dict) and c.get("fieldname") in keys}
		top = sorted(rows, key=lambda r: -flt(r.get("outstanding")))[:8]
		return {
			"totals": totals,
			"labels": labels,
			"top": [{"party_type": r.get("party_type"), "party": r.get("party"), "party_name": r.get("party_name") or r.get("party"), "outstanding": flt(r.get("outstanding"))} for r in top],
		}

	cash_accounts = frappe.get_list("Account", filters={"company": company, "account_type": ["in", ["Bank", "Cash"]], "is_group": 0, "disabled": 0},
	                                fields=["name", "account_name", "account_type", "account_currency"])
	tax_accounts = frappe.get_list("Account", filters={"company": company, "account_type": "Tax", "is_group": 0, "disabled": 0, "root_type": "Liability"},
	                               fields=["name", "account_name"])
	cash, taxes = [], []
	if frappe.has_permission("GL Entry", "read"):
		from erpnext.accounts.utils import get_balance_on

		cash = [{**a, "balance": flt(get_balance_on(a.name, to_date, company=company))} for a in cash_accounts]
		# A liability's balance is a credit: ERPNext answers it negative, the dashboard owes it positive.
		taxes = [{**a, "balance": -flt(get_balance_on(a.name, to_date, company=company))} for a in tax_accounts]

	working_capital = None
	if bs:
		groups = {r.get("account_name"): flt(r.get("total")) for r in bs.get("result") or [] if isinstance(r, dict)}
		if "Current Assets" in groups and "Current Liabilities" in groups:
			working_capital = {"current_assets": groups["Current Assets"], "current_liabilities": groups["Current Liabilities"],
			                   "working_capital": groups["Current Assets"] - groups["Current Liabilities"]}

	budget_rows = []
	for r in (budget or {}).get("result") or []:
		if isinstance(r, dict) and r.get("account"):
			b = next((v for k, v in r.items() if k.startswith("budget_")), 0)
			a = next((v for k, v in r.items() if k.startswith("actual_")), 0)
			budget_rows.append({"budget_against": r.get("budget_against"), "account": r.get("account"), "budget": flt(b), "actual": flt(a),
			                    "utilisation": flt(flt(a) / flt(b) * 100, 1) if flt(b) else None})

	cc_rows = [
		{"cost_center": r.get("account"), "label": r.get("account_name"), "income": flt(r.get("income")), "expense": flt(r.get("expense")), "net": flt(r.get("gross_profit_loss"))}
		for r in (by_cc or {}).get("result") or []
		if isinstance(r, dict) and r.get("parent_account")
	]

	return {
		"company": company,
		"currency": frappe.get_cached_value("Company", company, "default_currency"),
		"from_date": from_date,
		"to_date": to_date,
		"profit_and_loss": pl and {
			"income": _summary(pl, "Total Income"),
			"expense": _summary(pl, "Total Expense"),
			"net_profit": _summary(pl, "Net Profit"),
			"chart": pl.get("chart"),
			"income_accounts": tree_rows({"result": [r for r in pl["result"] if isinstance(r, dict) and _root(r, pl) == "Income"]}, 1),
			"expense_groups": tree_rows({"result": [r for r in pl["result"] if isinstance(r, dict) and _root(r, pl) == "Expense"]}, 1),
			"expense_accounts": tree_rows({"result": [r for r in pl["result"] if isinstance(r, dict) and _root(r, pl) == "Expense"]}, 2),
		},
		"balance_sheet": bs and {
			"assets": _summary(bs, "Total Asset"),
			"liabilities": _summary(bs, "Total Liability"),
			"equity": _summary(bs, "Total Equity"),
			"provisional_profit": next((flt(r.get("value")) for r in bs.get("report_summary") or [] if str(r.get("label", "")).startswith("Provisional")), None),
		},
		"working_capital": working_capital,
		"cash_flow": cf and {
			"operations": _summary(cf, "Net Cash from Operations"),
			"investing": _summary(cf, "Net Cash from Investing"),
			"financing": _summary(cf, "Net Cash from Financing"),
			"net_change": _summary(cf, "Net Change in Cash"),
			"monthly": _monthly(cf),
		},
		"receivables": party_rows(ar),
		"payables": party_rows(ap),
		"cash_and_bank": cash,
		"cash_total": sum(a["balance"] for a in cash) if cash else None,
		"tax_liabilities": taxes,
		"tax_total": sum(a["balance"] for a in taxes) if taxes else None,
		"budget": {"fiscal_year": fy, "rows": budget_rows} if budget is not None else None,
		"cost_centers": cc_rows if by_cc else None,
		"unavailable": unavailable,
	}


def _root(row, out):
	"""The root type of a P&L row: its own account's, read once per report run."""
	cache = out.setdefault("_roots", {})
	account = row.get("account")
	if not account or not row.get("parent_account") and not row.get("is_group"):
		return None
	if account not in cache:
		cache[account] = frappe.get_cached_value("Account", account, "root_type")
	return cache[account]


def _monthly(cf):
	"""Net change in cash per period, from the Cash Flow report's own closing row."""
	last = next((r for r in reversed(cf.get("result") or []) if isinstance(r, dict) and "Net Change in Cash" in str(r.get("section") or "")), None)
	if not last:
		return []
	periods = [c for c in cf.get("columns") or [] if isinstance(c, dict) and c.get("fieldtype") == "Currency" and c.get("fieldname") != "total"]
	return [{"period": _(c.get("label")), "net_change": flt(last.get(c.get("fieldname")))} for c in periods]


# --- approvals and audit ----------------------------------------------------------------------

_SUMMARY_FIELDS = {
	"Journal Entry": ["title", "total_debit as amount", "posting_date", "voucher_type as kind"],
	"Payment Entry": ["party_name as title", "paid_amount as amount", "posting_date", "payment_type as kind"],
	"Purchase Invoice": ["supplier_name as title", "grand_total as amount", "posting_date", "bill_no as kind"],
	"Sales Invoice": ["customer_name as title", "grand_total as amount", "posting_date", "customer as kind"],
	"Budget": ["budget_against as title", "name as kind"],
	"Period Closing Voucher": ["remarks as title", "transaction_date as posting_date"],
}


def _summarise(doctype, name):
	fields = _SUMMARY_FIELDS.get(doctype, [])
	row = frappe.db.get_value(doctype, name, ["owner", "modified", "docstatus", *fields], as_dict=True) or {}
	return row


@frappe.whitelist()
def approvals():
	"""What waits on this person in the finance workflows, and what they have sent and is waiting."""
	_only_finance()
	governed = [dt for dt in DOCTYPES if get_workflow_name(dt) and frappe.has_permission(dt, "read")]
	actions = frappe.get_list(
		"Workflow Action",
		filters={"status": "Open", "reference_doctype": ["in", governed or [""]]},
		fields=["reference_doctype", "reference_name", "workflow_state", "creation"],
		order_by="creation asc",
		limit_page_length=200,
	)
	seen, waiting = set(), []
	for a in actions:
		key = (a.reference_doctype, a.reference_name)
		if key in seen or not frappe.has_permission(a.reference_doctype, "read", a.reference_name):
			continue
		seen.add(key)
		doc = frappe.get_doc(a.reference_doctype, a.reference_name)
		moves = [t.action for t in get_transitions(doc)] if doc.docstatus < 2 else []
		if not moves:
			continue
		waiting.append({"doctype": a.reference_doctype, "name": a.reference_name, "state": a.workflow_state, "since": a.creation,
		                "moves": moves, **_summarise(a.reference_doctype, a.reference_name)})

	mine = []
	for dt in governed:
		for row in frappe.get_list(dt, filters={"owner": frappe.session.user, "docstatus": 0}, fields=["name", "workflow_state", "modified"],
		                           order_by="modified desc", limit_page_length=50):
			mine.append({"doctype": dt, "name": row.name, "state": row.workflow_state, "since": row.modified, **_summarise(dt, row.name)})
	mine.sort(key=lambda r: str(r["since"]), reverse=True)
	return {"waiting": waiting, "mine": mine, "workflows": {dt: get_workflow_name(dt) for dt in governed}}


_AUDITED = ("Journal Entry", "Sales Invoice", "Purchase Invoice", "Payment Entry", "Account", "Company", "Accounts Settings",
            "Fiscal Year", "Accounting Period", "Period Closing Voucher", "Budget", "Bank Account", "Cost Center",
            "Sales Taxes and Charges Template", "Purchase Taxes and Charges Template", "Asset", "Mode of Payment")


@frappe.whitelist()
def audit(from_date=None, to_date=None, doctype=None, user=None, kind=None, start=0, page_length=100):
	"""The finance audit trail: changes, workflow moves, submissions, cancellations, amendments.

	Read from Frappe's own records -- Version (every saved change), the Workflow comments
	apply_workflow writes, and the documents' docstatus and amended_from -- for the documents this
	person may read. Nothing here is recorded by FoLT; it is the history the documents already keep.
	"""
	_only_finance()
	# Only the doctypes this person may read: an Auditor reads the ledger, not every master.
	doctypes = [dt for dt in ([doctype] if doctype else list(_AUDITED)) if _doctype(dt) and frappe.has_permission(dt, "read")]
	if not doctypes:
		return {"events": [], "more": False}
	start, page_length = cint(start), min(cint(page_length) or 100, 500)
	window = []
	if from_date:
		window.append(["creation", ">=", f"{getdate(from_date)} 00:00:00"])
	if to_date:
		window.append(["creation", "<=", f"{getdate(to_date)} 23:59:59"])
	who = [["owner", "=", user]] if user else []
	events = []

	if kind in (None, "", "change"):
		for v in frappe.get_all("Version", filters=[["ref_doctype", "in", doctypes], *window, *who],
		                        fields=["ref_doctype", "docname", "owner", "creation", "data"], order_by="creation desc",
		                        limit_page_length=start + page_length):
			events.append({"kind": "change", "doctype": v.ref_doctype, "name": v.docname, "user": v.owner, "when": v.creation, "detail": _version_summary(v.data)})
	if kind in (None, "", "workflow"):
		for c in frappe.get_all("Comment", filters=[["reference_doctype", "in", doctypes], ["comment_type", "in", ["Workflow", "Comment", "Info"]], *window,
		                                            *([["comment_email", "=", user]] if user else [])],
		                        fields=["reference_doctype", "reference_name", "comment_email", "owner", "creation", "content", "comment_type"],
		                        order_by="creation desc", limit_page_length=start + page_length):
			events.append({"kind": "workflow" if c.comment_type == "Workflow" else "comment", "doctype": c.reference_doctype, "name": c.reference_name,
			               "user": c.comment_email or c.owner, "when": c.creation, "detail": frappe.utils.strip_html(c.content or "")[:300]})
	if kind in (None, "", "cancelled", "amended"):
		for dt in doctypes:
			meta = frappe.get_meta(dt)
			if not meta.is_submittable:
				continue
			mod_window = [["modified" if w[0] == "creation" else w[0], w[1], w[2]] for w in window]
			if kind in (None, "", "cancelled"):
				for r in frappe.get_list(dt, filters=[["docstatus", "=", 2], *mod_window, *([["modified_by", "=", user]] if user else [])],
				                         fields=["name", "modified", "modified_by"], order_by="modified desc", limit_page_length=start + page_length):
					events.append({"kind": "cancelled", "doctype": dt, "name": r.name, "user": r.modified_by, "when": r.modified, "detail": ""})
			if kind in (None, "", "amended"):
				for r in frappe.get_list(dt, filters=[["amended_from", "is", "set"], *window, *who], fields=["name", "creation", "owner", "amended_from"],
				                         order_by="creation desc", limit_page_length=start + page_length):
					events.append({"kind": "amended", "doctype": dt, "name": r.name, "user": r.owner, "when": r.creation, "detail": _("amends {0}").format(r.amended_from)})

	readable = {}

	def can_read(dt, name):
		key = (dt, name)
		if key not in readable:
			readable[key] = bool(frappe.db.exists(dt, name)) and frappe.has_permission(dt, "read", name) if dt != name else frappe.has_permission(dt, "read")
		return readable[key]

	events = [e for e in events if can_read(e["doctype"], e["name"])]
	events.sort(key=lambda e: str(e["when"]), reverse=True)
	return {"events": events[start : start + page_length], "more": len(events) > start + page_length}


def _version_summary(data):
	try:
		d = frappe.parse_json(data) or {}
	except Exception:  # noqa: BLE001
		return ""
	parts = []
	for field, old, new in (d.get("changed") or [])[:6]:
		parts.append(f"{field}: {old if old not in (None, '') else '—'} → {new if new not in (None, '') else '—'}")
	if d.get("added"):
		parts.append(_("{0} row(s) added").format(len(d["added"])))
	if d.get("removed"):
		parts.append(_("{0} row(s) removed").format(len(d["removed"])))
	if d.get("row_changed"):
		parts.append(_("{0} row(s) changed").format(len(d["row_changed"])))
	return "; ".join(parts) or _("created")


# --- period closing ---------------------------------------------------------------------------


@frappe.whitelist()
def closing_review(company, fiscal_year=None, to_date=None):
	"""The period-end checklist: what is still open in the period, counted from ERPNext's records.

	Each line is a count of documents with a link to them, never a verdict: whether a period may be
	closed is the controller's decision, taken on the Period Closing Voucher, by a person.
	"""
	_only_finance()
	if fiscal_year:
		start, end = frappe.db.get_value("Fiscal Year", fiscal_year, ["year_start_date", "year_end_date"])
	else:
		end = getdate(to_date or nowdate())
		from erpnext.accounts.utils import get_fiscal_year

		fiscal_year, start, _end = get_fiscal_year(end, company=company)
	start, end = str(start), str(end)
	period = [["company", "=", company], ["posting_date", "between", [start, end]]]

	def count(dt, filters):
		if not frappe.has_permission(dt, "read"):
			return None
		return frappe.get_list(dt, fields=[{"COUNT": "*", "as": "n"}], filters=filters)[0].n

	checks = []
	for dt in ("Journal Entry", "Sales Invoice", "Purchase Invoice", "Payment Entry"):
		checks.append({"key": f"drafts:{dt}", "label": _("{0} drafts in the period").format(_(dt)), "count": count(dt, [*period, ["docstatus", "=", 0]]),
		               "doctype": dt, "filters": [["docstatus", "=", 0], ["posting_date", "between", [start, end]]]})
	checks.append({"key": "bank:unreconciled", "label": _("Bank transactions not reconciled"),
	               "count": count("Bank Transaction", [["company", "=", company], ["date", "<=", end], ["docstatus", "=", 1], ["unallocated_amount", ">", 0]]),
	               "doctype": "Bank Transaction", "filters": [["date", "<=", end], ["docstatus", "=", 1], ["unallocated_amount", ">", 0]]})
	checks.append({"key": "sales:overdue", "label": _("Sales invoices overdue at period end"),
	               "count": count("Sales Invoice", [["company", "=", company], ["docstatus", "=", 1], ["outstanding_amount", ">", 0], ["due_date", "<", end]]),
	               "doctype": "Sales Invoice", "filters": [["docstatus", "=", 1], ["outstanding_amount", ">", 0], ["due_date", "<", end]]})
	checks.append({"key": "purchase:overdue", "label": _("Purchase invoices overdue at period end"),
	               "count": count("Purchase Invoice", [["company", "=", company], ["docstatus", "=", 1], ["outstanding_amount", ">", 0], ["due_date", "<", end]]),
	               "doctype": "Purchase Invoice", "filters": [["docstatus", "=", 1], ["outstanding_amount", ">", 0], ["due_date", "<", end]]})
	depreciation = None
	if frappe.has_permission("Asset", "read") and frappe.db.table_exists("Depreciation Schedule"):
		depreciation = frappe.db.sql(
			"""select count(*) from `tabDepreciation Schedule` ds join `tabAsset Depreciation Schedule` ads on ads.name = ds.parent
			where ads.docstatus = 1 and ads.company = %s and ds.schedule_date <= %s and ifnull(ds.journal_entry, '') = ''""",
			(company, end),
		)[0][0]
	checks.append({"key": "assets:depreciation", "label": _("Depreciation due and not yet posted"), "count": depreciation, "doctype": "Asset", "filters": []})

	tb = _quiet("Trial Balance", {"company": company, "fiscal_year": fiscal_year, "from_date": start, "to_date": end, "show_zero_values": 0})
	total = next((r for r in reversed((tb or {}).get("result") or []) if isinstance(r, dict) and _cell(r.get("account")) == "Total"), None)
	trial_balance = total and {
		"debit": flt(total.get("closing_debit")), "credit": flt(total.get("closing_credit")),
		"balanced": abs(flt(total.get("closing_debit")) - flt(total.get("closing_credit"))) < 0.005,
	}
	return {
		"company": company,
		"fiscal_year": fiscal_year,
		"from_date": start,
		"to_date": end,
		"checks": checks,
		"trial_balance": trial_balance,
		"closing_vouchers": frappe.get_list("Period Closing Voucher", filters={"company": company}, fields=["name", "transaction_date", "fiscal_year", "docstatus", "period_end_date"],
		                                    order_by="transaction_date desc", limit_page_length=20) if frappe.has_permission("Period Closing Voucher", "read") else [],
		"accounting_periods": frappe.get_list("Accounting Period", filters={"company": company}, fields=["name", "period_name", "start_date", "end_date"],
		                                      order_by="start_date desc", limit_page_length=24) if frappe.has_permission("Accounting Period", "read") else [],
		"frozen_until": frappe.db.get_single_value("Accounts Settings", "acc_frozen_upto") if frappe.get_meta("Accounts Settings").has_field("acc_frozen_upto") else frappe.db.get_value("Company", company, "accounts_frozen_till_date"),
	}
