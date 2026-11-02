"""The FoLT app's Procurement module, server half (folt-workflow-ui /procurement; BFF package
`procurement`; ADR-008).

Procurement in the app is ERPNext's buying cycle reached without the Desk: Supplier, Material
Request (the purchase requisition), Request for Quotation, Supplier Quotation, Purchase Order,
Purchase Receipt and Purchase Invoice, plus FoLT's own award documents (Procurement Committee
Evaluation, Derogation Waiver Request). Nothing here keeps a ledger, a stock balance or a status of
its own. Every figure is read from those documents as the person asking may read them. Every write
is ERPNext's own save, mapper or workflow step, run as that person -- so the award gate
(purchase_order.require_award_authority), the approval workflows and finance_approvals' guard all
apply exactly as they do in the Desk.

What this module adds is the edge, on the pattern of finance_portal (ADR-005):

*Which doctypes.* DOCTYPES is the allow-list; READ_ONLY are listed and opened but never written
from here (payments are Finance's). The BFF in front holds the same list against the spec's
ProcurementDoctype enum.

*Who.* The procurement, stores and finance roles (STAFF) reach everything on the list, and Frappe
then checks each document. Anybody else who is an Employee reaches only the requisition
(REQUESTER_DOCTYPES) -- raising a purchase request is open to staff, the rest of the buying cycle
is not.

*Composition.* A supplier comes back with its contacts, addresses, bank accounts (masked unless
the reader manages them), documents and ERPNext's own dashboard figures in one call; the dashboard
is counts and sums over the buying documents for one company and period.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, date_diff, flt, getdate, nowdate

from folt_customizations import portal_common as _common
from folt_customizations.portal_common import parse_json as _json

# The roles that work the buying cycle. Frappe's own permissions decide each document.
STAFF = (
	"Purchase User", "Purchase Manager", "Purchase Master Manager", "Stock User", "Stock Manager",
	"Procurement Committee", "Operations Support Officer", "Head of Programs", "Head of Finance", "Finance Manager",
	"Finance Officer", "Executive Director", "Accounts User", "Accounts Manager", "Auditor", "System Manager",
)
REQUESTERS = ("Employee",)
# Bank details in full: the people who keep suppliers' payment details. Everybody else who may
# read a bank account sees its last four characters.
BANK_KEEPERS = ("Accounts Manager", "System Manager")

# doctype -> (group, list columns, searched fields, date field). Columns a doctype's meta does not
# have on this version are dropped at run time (portal_common.columns).
DOCTYPES = {
	# suppliers
	"Supplier": ("suppliers", ["supplier_name", "supplier_group", "supplier_type", "country", "tax_id", "default_currency", "disabled", "on_hold"],
	             ["name", "supplier_name", "tax_id", "email_id", "mobile_no"], None),
	"Contact": ("suppliers", ["full_name", "email_id", "mobile_no", "phone", "designation", "company_name"], ["name", "full_name", "email_id", "mobile_no"], None),
	"Address": ("suppliers", ["address_title", "address_type", "address_line1", "city", "country"], ["name", "address_title", "address_line1", "city"], None),
	"Bank Account": ("suppliers", ["account_name", "bank", "party_type", "party", "is_default", "disabled"], ["name", "account_name", "bank", "party"], None),
	"Supplier Group": ("suppliers", ["supplier_group_name", "parent_supplier_group", "is_group", "payment_terms"], ["name", "supplier_group_name"], None),
	"Supplier Scorecard": ("suppliers", ["supplier", "supplier_score", "status", "period"], ["name", "supplier"], None),
	# requisitions
	"Material Request": ("requisitions", ["transaction_date", "schedule_date", "material_request_type", "folt_department", "status", "per_ordered", "per_received"],
	                     ["name", "title", "folt_department"], "transaction_date"),
	# sourcing
	"Request for Quotation": ("sourcing", ["transaction_date", "schedule_date", "folt_department", "status"], ["name", "message_for_supplier"], "transaction_date"),
	"Supplier Quotation": ("sourcing", ["supplier_name", "transaction_date", "valid_till", "grand_total", "currency", "status"], ["name", "supplier", "supplier_name"], "transaction_date"),
	"Procurement Committee Evaluation": ("sourcing", ["request_for_quotation", "recommended_supplier", "workflow_state"], ["name", "request_for_quotation", "recommended_supplier"], None),
	"Derogation Waiver Request": ("sourcing", ["supplier", "estimated_value", "project", "workflow_state"], ["name", "supplier", "organisation_project_name"], None),
	# orders
	"Purchase Order": ("orders", ["supplier_name", "transaction_date", "schedule_date", "grand_total", "currency", "per_received", "per_billed", "status"],
	                   ["name", "supplier", "supplier_name", "folt_department"], "transaction_date"),
	# receipts
	"Purchase Receipt": ("receipts", ["supplier_name", "posting_date", "supplier_delivery_note", "grand_total", "per_billed", "status"],
	                     ["name", "supplier", "supplier_name", "supplier_delivery_note"], "posting_date"),
	# invoices
	"Purchase Invoice": ("invoices", ["supplier_name", "bill_no", "posting_date", "due_date", "grand_total", "outstanding_amount", "currency", "status"],
	                     ["name", "supplier", "supplier_name", "bill_no"], "posting_date"),
	"Payment Entry": ("invoices", ["posting_date", "party_name", "paid_amount", "mode_of_payment", "reference_no"], ["name", "party", "party_name", "reference_no"], "posting_date"),
}

# Opened and listed here, written in Finance.
READ_ONLY = {"Payment Entry", "Supplier Scorecard"}
REQUESTER_DOCTYPES = {"Material Request"}

# The doctypes on the buying chain, in order: the dashboard, approvals and audit read these.
CHAIN = ("Material Request", "Request for Quotation", "Supplier Quotation", "Procurement Committee Evaluation",
         "Derogation Waiver Request", "Purchase Order", "Purchase Receipt", "Purchase Invoice")

# Link searches into these never answer from Procurement: payroll and people's records are HR's.
_HR_ONLY = {
	"Salary Slip", "Salary Structure", "Salary Structure Assignment", "Payroll Entry", "Additional Salary",
	"Employee Tax Exemption Declaration", "Employee Tax Exemption Proof Submission", "Leave Application",
}

# ERPNext's own read-only helpers a buying form calls.
LOOKUPS = {
	"erpnext.stock.get_item_details.get_item_details",
	"erpnext.accounts.party.get_party_details",
	"erpnext.accounts.party.get_due_date",
	"erpnext.setup.utils.get_exchange_rate",
	"erpnext.controllers.accounts_controller.get_taxes_and_charges",
	"erpnext.controllers.accounts_controller.get_default_taxes_and_charges",
	"erpnext.controllers.accounts_controller.get_payment_terms",
	"folt_customizations.material_request.activity_defaults",
	# ERPNext's own makers: an unsaved RFQ from an approved requisition, an unsaved bid from a sent
	# RFQ for one supplier. They write nothing; the form saves what the person reviewed.
	"erpnext.stock.doctype.material_request.material_request.make_request_for_quotation",
	"erpnext.buying.doctype.request_for_quotation.request_for_quotation.make_supplier_quotation_from_rfq",
}
# What an Employee with no procurement role may look up: the requisition's own helpers.
REQUESTER_LOOKUPS = {"folt_customizations.material_request.activity_defaults", "erpnext.stock.get_item_details.get_item_details"}

# The setters each controller's own validate() runs, in its order, minus the checks.
_PREVIEW = {
	"Material Request": ("set_missing_values",),
	"Supplier Quotation": ("set_missing_values", "calculate_taxes_and_totals"),
	"Purchase Order": ("set_missing_values", "calculate_taxes_and_totals", "set_total_in_words"),
	"Purchase Receipt": ("set_missing_values", "calculate_taxes_and_totals", "set_total_in_words"),
	"Purchase Invoice": ("set_missing_values", "calculate_taxes_and_totals", "set_total_in_words"),
}


# --- the edge -------------------------------------------------------------------------------


def _roles():
	return set(frappe.get_roles())


def _is_staff() -> bool:
	return bool(_roles() & set(STAFF))


def _only_procurement(doctype=None):
	"""The role floor: procurement staff, or an Employee raising a requisition."""
	if _is_staff():
		return
	if _roles() & set(REQUESTERS) and (doctype is None or doctype in REQUESTER_DOCTYPES):
		return
	frappe.throw(_("Procurement is open to the procurement, stores and finance roles."), frappe.PermissionError)


def _doctype(doctype: str) -> str:
	if doctype not in DOCTYPES:
		frappe.throw(_("{0} is not part of Procurement.").format(doctype), frappe.PermissionError)
	_only_procurement(doctype)
	return doctype


def _writable(doctype: str) -> str:
	_doctype(doctype)
	if doctype in READ_ONLY:
		frappe.throw(_("{0} is kept in Finance; Procurement only reads it.").format(_(doctype)), frappe.PermissionError)
	return doctype


def _keeps_bank_details() -> bool:
	return bool(_roles() & set(BANK_KEEPERS))


def _mask_bank(doctype, data):
	if doctype == "Bank Account" and not _keeps_bank_details():
		for field in ("bank_account_no", "iban", "branch_code"):
			data[field] = _common.mask(data.get(field))


def _may(doctype, ptype="read"):
	try:
		return bool(frappe.has_permission(doctype, ptype))
	except Exception:  # noqa: BLE001 -- a doctype missing on this version is simply not offered
		return False


# --- context ----------------------------------------------------------------------------------


@frappe.whitelist()
def context():
	"""What every Procurement screen needs to know before it asks anything else."""
	_only_procurement()
	from frappe.model.workflow import get_workflow_name

	companies = _companies()
	default_company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value("Global Defaults", "default_company")
	if companies and default_company not in {c.name for c in companies}:
		default_company = companies[0].name

	staff = _is_staff()
	visible = [dt for dt in DOCTYPES if staff or dt in REQUESTER_DOCTYPES]
	capabilities = {}
	for doctype in visible:
		capabilities[doctype] = {ptype: _may(doctype, ptype) for ptype in ("read", "create", "write", "submit", "cancel", "delete")}
		if doctype in READ_ONLY:
			capabilities[doctype].update({"create": False, "write": False, "submit": False, "cancel": False, "delete": False})

	employee = frappe.db.get_value("Employee", {"user_id": frappe.session.user, "status": "Active"}, ["name", "employee_name", "department", "company"], as_dict=True)
	roles = _roles()
	return {
		"user": frappe.session.user,
		"companies": companies,
		"default_company": default_company,
		"capabilities": capabilities,
		"workflows": {dt: get_workflow_name(dt) for dt in visible if get_workflow_name(dt)},
		"employee": employee,
		"departments": frappe.get_list("Department", filters={"is_group": 0}, fields=["name", "department_name", "company"], order_by="department_name") if _may("Department") else [],
		"roles": {
			"staff": staff,
			"requester": bool(roles & set(REQUESTERS)),
			"buyer": bool(roles & {"Purchase User", "Purchase Manager", "System Manager"}),
			"manager": bool(roles & {"Purchase Manager", "System Manager"}),
			"supplier_manager": bool(roles & {"Purchase Master Manager", "Purchase Manager", "System Manager"}),
			"receiver": bool(roles & {"Stock User", "Stock Manager", "System Manager"}),
			"finance": bool(roles & {"Accounts User", "Accounts Manager", "Auditor", "System Manager"}),
			"bank_keeper": _keeps_bank_details(),
			"auditor": "Auditor" in roles,
		},
	}


def _companies():
	"""The companies a procurement screen may be scoped to: their names and currencies, which every
	buying form needs, whether or not the reader may open the Company record itself (a Purchase
	Manager may not). A User Permission on Company still narrows the list."""
	from frappe.permissions import get_user_permissions

	allowed = [p.get("doc") for p in get_user_permissions(frappe.session.user).get("Company", [])]
	filters = {"name": ["in", allowed]} if allowed else {}
	return frappe.get_all("Company", filters=filters, fields=["name", "abbr", "default_currency", "country", "cost_center"], order_by="name")


# --- lists and records ------------------------------------------------------------------------


@frappe.whitelist()
def records(doctype, q=None, filters=None, order_by=None, start=0, page_length=20):
	"""One page of a Procurement list, as the person may see it, with its total."""
	_doctype(doctype)
	_group, wanted, search, _date = DOCTYPES[doctype]
	extra = []
	if doctype == "Material Request":
		# The requisition list is the purchase requests; ERPNext's other purposes (transfer, issue,
		# manufacture) are stores' business.
		extra.append(["Material Request", "material_request_type", "=", "Purchase"])
	if doctype in ("Contact", "Address", "Bank Account"):
		extra.extend(_supplier_scope(doctype))
	return _common.list_view(doctype, wanted, search, q=q, raw_filters=filters, order_by=order_by, start=start, page_length=page_length, extra_filters=extra)


def _supplier_scope(doctype):
	"""Contacts, addresses and bank accounts here are suppliers' -- never staff's or customers'."""
	if doctype == "Bank Account":
		return [["Bank Account", "party_type", "=", "Supplier"]]
	return [["Dynamic Link", "link_doctype", "=", "Supplier"]]


def _assert_suppliers(doctype, name):
	"""A contact, address or bank account opened from Procurement belongs to a supplier."""
	if doctype == "Bank Account":
		if frappe.db.get_value("Bank Account", name, "party_type") != "Supplier":
			frappe.throw(_("Procurement opens suppliers' bank accounts only."), frappe.PermissionError)
	elif doctype in ("Contact", "Address"):
		if not frappe.db.exists("Dynamic Link", {"parenttype": doctype, "parent": name, "link_doctype": "Supplier"}):
			frappe.throw(_("Procurement opens suppliers' contacts and addresses only."), frappe.PermissionError)


@frappe.whitelist()
def record(doctype, name=None):
	"""A document with everything its screen shows: workflow moves, timeline, attachments."""
	_doctype(doctype)
	_assert_suppliers(doctype, name)
	out = _common.record_view(doctype, name, masker=_mask_bank)
	if doctype in READ_ONLY:
		out["permissions"].update({"write": False, "create": False, "submit": False, "cancel": False, "amend": False, "delete": False})
	return out


@frappe.whitelist()
def meta(doctype):
	"""The fields of a Procurement doctype (and its child tables), for a form drawn from them."""
	_doctype(doctype)
	return _common.meta_view(doctype)


# --- writes -----------------------------------------------------------------------------------

# A supplier's primary contact and address are made by ERPNext itself on insert
# (Supplier.create_primary_contact / create_primary_address) from these values.
_SUPPLIER_QUICK_ENTRY = ("email_id", "mobile_no", "address_line1", "address_line2", "city", "state", "pincode", "country")


@frappe.whitelist(methods=["POST"])
def save(doctype, doc, name=None):
	"""Save a draft (new or existing). Never submits (portal_common.save_doc)."""
	_writable(doctype)
	values = _json(doc, {}) or {}
	if doctype in ("Contact", "Address"):
		_links_to_suppliers_only(values)
		if name:
			_assert_suppliers(doctype, name)
	if doctype == "Bank Account":
		if (values.get("party_type") or "Supplier") != "Supplier" or (name and frappe.db.get_value("Bank Account", name, "party_type") != "Supplier"):
			frappe.throw(_("Procurement keeps suppliers' bank accounts only."), frappe.PermissionError)
		if name and not _keeps_bank_details():
			# What this reader was shown is masked: never write the mask back over the number.
			for field in ("bank_account_no", "iban", "branch_code"):
				values.pop(field, None)
	if doctype == "Supplier" and not name:
		quick = {k: values.get(k) for k in _SUPPLIER_QUICK_ENTRY if values.get(k)}
		if quick.get("address_line1") and not (quick.get("city") and quick.get("country")):
			frappe.throw(_("An address needs its town or city and its country."))
	if doctype == "Material Request" and not name:
		values.setdefault("material_request_type", "Purchase")
		_requester_defaults(values)
	target = _common.save_doc(doctype, values, name)
	return {"name": target.name}


def _links_to_suppliers_only(values):
	links = values.get("links")
	if links is None:
		return
	if not links or any((row or {}).get("link_doctype") != "Supplier" for row in links):
		frappe.throw(_("A contact or address kept from Procurement is linked to suppliers only."), frappe.PermissionError)


def _requester_defaults(values):
	"""A new requisition is the requester's department's unless they say otherwise."""
	if values.get("folt_department"):
		return
	dept = frappe.db.get_value("Employee", {"user_id": frappe.session.user, "status": "Active"}, "department")
	if dept:
		values["folt_department"] = dept


@frappe.whitelist(methods=["POST"])
def preview(doctype, doc):
	"""ERPNext's own defaults and totals for an unsaved draft. Writes nothing."""
	_writable(doctype)
	after = None
	if doctype == "Material Request":
		from folt_customizations.material_request import preview as after
	return _common.preview_doc(doctype, doc, _PREVIEW.get(doctype, ("set_missing_values",)), after)


@frappe.whitelist(methods=["POST"])
def act(doctype, name, action, transition=None, reason=None):
	"""Move a document: a workflow step, or ERPNext's submit / cancel / amend / delete."""
	_writable(doctype)
	_assert_suppliers(doctype, name)
	return _common.act_on(doctype, name, action, transition, reason)


@frappe.whitelist(methods=["POST"])
def attach(doctype, docname):
	"""Frappe's own upload_file, private, onto a Procurement document; Frappe checks the write right."""
	_writable(doctype)
	_assert_suppliers(doctype, docname)
	return _common.attach_file(doctype, docname)


@frappe.whitelist()
def copy(doctype, name):
	"""An unsaved copy of a document, to start a new one from (Duplicate). Writes nothing."""
	_writable(doctype)
	return _common.copy_view(doctype, name)


# --- searches and helpers ---------------------------------------------------------------------


@frappe.whitelist()
def link_search(doctype, txt="", filters=None, page_length=20):
	"""Frappe's own link search, minus the doctypes that are HR's alone."""
	_only_procurement()
	if doctype in _HR_ONLY:
		frappe.throw(_("{0} belongs to HR.").format(doctype), frappe.PermissionError)
	return _common.link_search(doctype, txt, filters, page_length)


@frappe.whitelist(methods=["POST"])
def lookup(method, args=None):
	"""One of ERPNext's read-only helpers a buying form uses (LOOKUPS)."""
	_only_procurement()
	if not _is_staff() and method not in REQUESTER_LOOKUPS:
		frappe.throw(_("{0} is for the procurement roles.").format(method), frappe.PermissionError)
	out = _common.call_allowed(method, args, LOOKUPS, _("{0} is not available from Procurement."))
	# A maker answers an unsaved Document: the form takes it as plain values, dates as strings.
	return out.as_dict(convert_dates_to_str=True) if hasattr(out, "as_dict") else out


# --- suppliers --------------------------------------------------------------------------------


def _linked(doctype, supplier):
	names = frappe.get_all("Dynamic Link", filters={"parenttype": doctype, "link_doctype": "Supplier", "link_name": supplier}, pluck="parent")
	if not _may(doctype):
		return None
	if not names:
		return []
	fields = {
		"Contact": ["name", "full_name", "email_id", "mobile_no", "phone", "designation", "is_primary_contact"],
		"Address": ["name", "address_title", "address_type", "address_line1", "address_line2", "city", "state", "pincode", "country", "is_primary_address"],
	}[doctype]
	return frappe.get_list(doctype, filters={"name": ["in", names]}, fields=fields, order_by="modified desc")


def _documents(doctype, filters, fields, order_by, limit=10):
	"""Recent documents and their count, or None when the reader may not list the doctype."""
	if not _may(doctype):
		return None
	rows = frappe.get_list(doctype, filters=filters, fields=fields, order_by=order_by, limit_page_length=limit)
	total = frappe.get_list(doctype, filters=filters, fields=[{"COUNT": "*", "as": "n"}])
	return {"rows": rows, "total": total[0].n if total else 0}


@frappe.whitelist()
def supplier_profile(name):
	"""One supplier with everything its profile shows, each part as the reader may see it."""
	_only_procurement("Supplier")
	supplier = frappe.get_doc("Supplier", name)
	supplier.check_permission("read")
	party = {"supplier": name}

	bank = None
	if _may("Bank Account"):
		bank = frappe.get_list("Bank Account", filters={"party_type": "Supplier", "party": name},
		                       fields=["name", "account_name", "bank", "bank_account_no", "iban", "branch_code", "is_default", "disabled"])
		if not _keeps_bank_details():
			for row in bank:
				for field in ("bank_account_no", "iban", "branch_code"):
					row[field] = _common.mask(row.get(field))

	figures = None
	if _may("Purchase Invoice"):
		# ERPNext's own supplier dashboard figures (billed this year, unpaid), per company.
		from erpnext.accounts.party import get_dashboard_info

		figures = get_dashboard_info("Supplier", name)

	payments = None
	if _may("Payment Entry"):
		payments = _documents("Payment Entry", {"party_type": "Supplier", "party": name, "docstatus": 1},
		                      ["name", "posting_date", "paid_amount", "paid_from_account_currency as currency", "mode_of_payment", "reference_no"], "posting_date desc")

	scorecard = None
	if _may("Supplier Scorecard") and frappe.db.exists("Supplier Scorecard", name):
		sc = frappe.get_doc("Supplier Scorecard", name)
		scorecard = {"name": sc.name, "score": flt(sc.supplier_score), "status": sc.status, "period": sc.period,
		             "standings": [{"standing_name": s.standing_name, "min_grade": s.min_grade, "max_grade": s.max_grade} for s in sc.standings or []]}

	items = []
	if _may("Item"):
		codes = frappe.get_all("Item Supplier", filters={"supplier": name, "parenttype": "Item"}, pluck="parent")
		codes += frappe.get_all("Item Default", filters={"default_supplier": name, "parenttype": "Item"}, pluck="parent")
		if codes:
			items = frappe.get_list("Item", filters={"name": ["in", list(set(codes))]}, fields=["name", "item_name", "item_group", "stock_uom"], order_by="item_name", limit_page_length=50)

	return {
		"record": _common.record_view("Supplier", name),
		"status": "Blocked" if supplier.on_hold else ("Inactive" if supplier.disabled else "Active"),
		"qualified_until": supplier.get("folt_qualified_until"),
		"qualified": _qualified(supplier),
		"groups": [supplier.supplier_group] + [r.supplier_group for r in supplier.get("folt_additional_supplier_groups") or [] if r.supplier_group != supplier.supplier_group],
		"contacts": _linked("Contact", name),
		"addresses": _linked("Address", name),
		"bank_accounts": bank,
		"figures": figures,
		"quotations": _documents("Supplier Quotation", party, ["name", "transaction_date", "valid_till", "grand_total", "currency", "status", "docstatus"], "transaction_date desc"),
		"orders": _documents("Purchase Order", party, ["name", "transaction_date", "schedule_date", "grand_total", "currency", "per_received", "per_billed", "status", "docstatus"], "transaction_date desc"),
		"receipts": _documents("Purchase Receipt", party, ["name", "posting_date", "grand_total", "currency", "status", "docstatus"], "posting_date desc"),
		"invoices": _documents("Purchase Invoice", party, ["name", "bill_no", "posting_date", "due_date", "grand_total", "outstanding_amount", "currency", "status", "docstatus"], "posting_date desc"),
		"payments": payments,
		"waivers": _documents("Derogation Waiver Request", party, ["name", "estimated_value", "workflow_state", "modified"], "modified desc"),
		"scorecard": scorecard,
		"delivery": _delivery_record(name),
		"items": items,
	}


def _qualified(supplier):
	until = supplier.get("folt_qualified_until")
	if not until:
		return None
	return getdate(until) >= getdate(nowdate())


def _delivery_record(supplier):
	"""How the supplier's received orders arrived against their required-by date: each order's last
	receipt against the order's schedule_date. Read from the receipts; nothing is kept."""
	if not (_may("Purchase Order") and _may("Purchase Receipt")):
		return None
	orders = frappe.get_list("Purchase Order", filters={"supplier": supplier, "docstatus": 1, "per_received": [">=", 100]},
	                         fields=["name", "schedule_date"], order_by="transaction_date desc", limit_page_length=50)
	if not orders:
		return {"orders": 0, "on_time": 0, "late": 0, "average_days_late": None}
	last = _last_receipts([o.name for o in orders])
	on_time, late, days = 0, 0, []
	for o in orders:
		received = last.get(o.name)
		if not received or not o.schedule_date:
			continue
		lateness = date_diff(received, o.schedule_date)
		if lateness > 0:
			late += 1
			days.append(lateness)
		else:
			on_time += 1
	return {"orders": on_time + late, "on_time": on_time, "late": late, "average_days_late": round(sum(days) / len(days), 1) if days else None}


def _last_receipts(orders):
	"""The posting date of each order's latest submitted receipt."""
	if not orders:
		return {}
	rows = frappe.get_all("Purchase Receipt Item", filters={"purchase_order": ["in", orders], "docstatus": 1}, fields=["purchase_order", "parent"])
	receipts = {r.parent for r in rows}
	dates = dict(frappe.get_all("Purchase Receipt", filters={"name": ["in", list(receipts) or [""]]}, fields=["name", "posting_date"], as_list=True))
	out = {}
	for r in rows:
		d = dates.get(r.parent)
		if d and (r.purchase_order not in out or d > out[r.purchase_order]):
			out[r.purchase_order] = d
	return out


# --- the dashboard ----------------------------------------------------------------------------


def _count(doctype, filters, unavailable):
	if not _may(doctype):
		unavailable.append({"doctype": doctype, "reason": "permission"})
		return None
	rows = frappe.get_list(doctype, filters=filters, fields=[{"COUNT": "*", "as": "n"}])
	return rows[0].n if rows else 0


def _sum(doctype, field, filters):
	if not _may(doctype):
		return None
	rows = frappe.get_list(doctype, filters=filters, fields=[{"SUM": field, "as": "total"}])
	return flt(rows[0].total) if rows else 0.0


def _wf_states(doctype):
	"""The workflow's states other than its first and its terminal ones: where a document waits on
	an approver. Empty when the doctype has no workflow."""
	from frappe.model.workflow import get_workflow_name

	if not get_workflow_name(doctype):
		return []
	wf = frappe.get_cached_doc("Workflow", get_workflow_name(doctype))
	first = wf.states[0].state if wf.states else None
	return [s.state for s in wf.states if s.state != first and cint(s.doc_status) == 0]


@frappe.whitelist()
def dashboard(company, from_date, to_date, department=None, cost_center=None, project=None, supplier=None):
	"""Counts and sums over the buying documents for one company and period, as the reader may see
	them. Each figure is a count of ERPNext documents or a sum of their own totals; nothing is
	computed that ERPNext does not already hold on the document."""
	_only_procurement()
	if not _is_staff():
		frappe.throw(_("The procurement dashboard is for the procurement, stores and finance roles."), frappe.PermissionError)
	from_date, to_date = str(getdate(from_date)), str(getdate(to_date))
	today = nowdate()
	unavailable = []

	def narrowed(doctype, child=None, date_field=None, use_supplier=True):
		f = [[doctype, "company", "=", company]]
		if date_field:
			f.append([doctype, date_field, "between", [from_date, to_date]])
		if department and frappe.get_meta(doctype).has_field("folt_department"):
			f.append([doctype, "folt_department", "=", department])
		if supplier and use_supplier and frappe.get_meta(doctype).has_field("supplier"):
			f.append([doctype, "supplier", "=", supplier])
		if child and cost_center:
			f.append([child, "cost_center", "=", cost_center])
		if child and project:
			f.append([child, "project", "=", project])
		return f

	mr = narrowed("Material Request", "Material Request Item", use_supplier=False) + [["Material Request", "material_request_type", "=", "Purchase"]]
	mr_waiting = _wf_states("Material Request")
	po = narrowed("Purchase Order", "Purchase Order Item")
	po_waiting = _wf_states("Purchase Order")
	pr = narrowed("Purchase Receipt", "Purchase Receipt Item")
	pi = narrowed("Purchase Invoice", "Purchase Invoice Item")
	pi_waiting = _wf_states("Purchase Invoice")
	rfq = narrowed("Request for Quotation", "Request for Quotation Item", use_supplier=False)
	sq = narrowed("Supplier Quotation", "Supplier Quotation Item")

	counts = {
		"requisitions_draft": _count("Material Request", mr + [["Material Request", "docstatus", "=", 0]] + ([["Material Request", "workflow_state", "not in", mr_waiting]] if mr_waiting else []), unavailable),
		"requisitions_awaiting_approval": _count("Material Request", mr + [["Material Request", "workflow_state", "in", mr_waiting]], unavailable) if mr_waiting else None,
		"requisitions_to_order": _count("Material Request", mr + [["Material Request", "docstatus", "=", 1], ["Material Request", "status", "in", ["Pending", "Partially Ordered"]]], unavailable),
		"rfqs_open": _count("Request for Quotation", rfq + [["Request for Quotation", "docstatus", "=", 1]], unavailable),
		"rfqs_draft": _count("Request for Quotation", rfq + [["Request for Quotation", "docstatus", "=", 0]], unavailable),
		"quotations_received": _count("Supplier Quotation", sq + [["Supplier Quotation", "docstatus", "=", 1], ["Supplier Quotation", "transaction_date", "between", [from_date, to_date]]], unavailable),
		"orders_draft": _count("Purchase Order", po + [["Purchase Order", "docstatus", "=", 0]] + ([["Purchase Order", "workflow_state", "not in", po_waiting]] if po_waiting else []), unavailable),
		"orders_awaiting_approval": _count("Purchase Order", po + [["Purchase Order", "workflow_state", "in", po_waiting]], unavailable) if po_waiting else None,
		"orders_approved": _count("Purchase Order", po + [["Purchase Order", "docstatus", "=", 1], ["Purchase Order", "transaction_date", "between", [from_date, to_date]]], unavailable),
		"orders_awaiting_delivery": _count("Purchase Order", po + [["Purchase Order", "docstatus", "=", 1], ["Purchase Order", "per_received", "<", 100],
		                                                           ["Purchase Order", "status", "not in", ["Closed", "On Hold", "Completed", "Delivered"]]], unavailable),
		"deliveries_overdue": _count("Purchase Order", po + [["Purchase Order", "docstatus", "=", 1], ["Purchase Order", "per_received", "<", 100], ["Purchase Order", "schedule_date", "<", today],
		                                                     ["Purchase Order", "status", "not in", ["Closed", "On Hold", "Completed", "Delivered"]]], unavailable),
		"receipts_draft": _count("Purchase Receipt", pr + [["Purchase Receipt", "docstatus", "=", 0]], unavailable),
		"orders_to_bill": _count("Purchase Order", po + [["Purchase Order", "docstatus", "=", 1], ["Purchase Order", "per_billed", "<", 100], ["Purchase Order", "per_received", ">", 0],
		                                                 ["Purchase Order", "status", "not in", ["Closed", "On Hold"]]], unavailable),
		"invoices_draft": _count("Purchase Invoice", pi + [["Purchase Invoice", "docstatus", "=", 0]] + ([["Purchase Invoice", "workflow_state", "not in", pi_waiting]] if pi_waiting else []), unavailable),
		"invoices_awaiting_approval": _count("Purchase Invoice", pi + [["Purchase Invoice", "workflow_state", "in", pi_waiting]], unavailable) if pi_waiting else None,
		"invoices_unpaid": _count("Purchase Invoice", pi + [["Purchase Invoice", "docstatus", "=", 1], ["Purchase Invoice", "outstanding_amount", ">", 0]], unavailable),
		"invoices_overdue": _count("Purchase Invoice", pi + [["Purchase Invoice", "docstatus", "=", 1], ["Purchase Invoice", "outstanding_amount", ">", 0], ["Purchase Invoice", "due_date", "<", today]], unavailable),
	}

	spend = {
		"ordered": _sum("Purchase Order", "base_grand_total", po + [["Purchase Order", "docstatus", "=", 1], ["Purchase Order", "transaction_date", "between", [from_date, to_date]]]),
		"invoiced": _sum("Purchase Invoice", "base_grand_total", pi + [["Purchase Invoice", "docstatus", "=", 1], ["Purchase Invoice", "posting_date", "between", [from_date, to_date]]]),
		"outstanding": _sum("Purchase Invoice", "outstanding_amount", pi + [["Purchase Invoice", "docstatus", "=", 1], ["Purchase Invoice", "outstanding_amount", ">", 0]]),
	}

	breakdown = _spend_breakdown(po, from_date, to_date) if _may("Purchase Order") else None

	from folt_customizations.portal_common import approvals_view

	approvals = approvals_view(list(CHAIN), _summarise) if _is_staff() else {"waiting": [], "mine": []}
	return {
		"company": company,
		"currency": frappe.get_cached_value("Company", company, "default_currency"),
		"from_date": from_date,
		"to_date": to_date,
		"counts": counts,
		"spend": spend,
		"by_department": breakdown and breakdown["department"],
		"by_project": breakdown and breakdown["project"],
		"by_cost_center": breakdown and breakdown["cost_center"],
		"by_supplier": breakdown and breakdown["supplier"],
		"by_month": breakdown and breakdown["month"],
		"cycle_time": _cycle_time(po, from_date, to_date),
		"suppliers": _supplier_performance(po, from_date, to_date),
		"waiting_on_me": approvals["waiting"][:10],
		"recent": _recent(),
		"unavailable": unavailable,
	}


def _spend_breakdown(po_filters, from_date, to_date):
	"""Ordered spend in the period by department, project, cost centre, supplier and month: the
	order lines' own base_net_amount, added up over the orders the reader may see."""
	orders = frappe.get_list("Purchase Order", filters=po_filters + [["Purchase Order", "docstatus", "=", 1], ["Purchase Order", "transaction_date", "between", [from_date, to_date]]],
	                         fields=["name", "supplier", "supplier_name", "folt_department", "transaction_date", "base_net_total"], limit_page_length=5000)
	by_name = {o.name: o for o in orders}
	out = {"department": {}, "project": {}, "cost_center": {}, "supplier": {}, "month": {}}
	if not orders:
		return {k: [] for k in out}
	for o in orders:
		key = o.supplier
		row = out["supplier"].setdefault(key, {"key": key, "label": o.supplier_name or key, "total": 0.0, "orders": 0})
		row["total"] += flt(o.base_net_total)
		row["orders"] += 1
		month = str(o.transaction_date)[:7]
		m = out["month"].setdefault(month, {"key": month, "label": month, "total": 0.0, "orders": 0})
		m["total"] += flt(o.base_net_total)
		m["orders"] += 1
		d = o.folt_department or ""
		dep = out["department"].setdefault(d, {"key": d, "label": d or _("No department"), "total": 0.0, "orders": 0})
		dep["total"] += flt(o.base_net_total)
		dep["orders"] += 1
	for line in frappe.get_all("Purchase Order Item", filters={"parent": ["in", list(by_name)]}, fields=["parent", "project", "cost_center", "base_net_amount"]):
		for dim in ("project", "cost_center"):
			k = line.get(dim) or ""
			row = out[dim].setdefault(k, {"key": k, "label": k or _("Not set"), "total": 0.0, "orders": set()})
			row["total"] += flt(line.base_net_amount)
			row["orders"].add(line.parent)
	result = {}
	for dim, rows in out.items():
		vals = []
		for r in rows.values():
			r = dict(r)
			if isinstance(r["orders"], set):
				r["orders"] = len(r["orders"])
			r["total"] = flt(r["total"], 2)
			vals.append(r)
		result[dim] = sorted(vals, key=lambda r: r["key"]) if dim == "month" else sorted(vals, key=lambda r: -r["total"])[:12]
	return result


def _cycle_time(po_filters, from_date, to_date):
	"""Average days from requisition to order, and from order to its first receipt, over the orders
	submitted in the period -- each date read off the documents themselves."""
	if not _may("Purchase Order"):
		return None
	orders = frappe.get_list("Purchase Order", filters=po_filters + [["Purchase Order", "docstatus", "=", 1], ["Purchase Order", "transaction_date", "between", [from_date, to_date]]],
	                         fields=["name", "transaction_date"], limit_page_length=2000)
	if not orders:
		return {"requisition_to_order": None, "order_to_receipt": None, "orders": 0}
	dates = {o.name: o.transaction_date for o in orders}
	lines = frappe.get_all("Purchase Order Item", filters={"parent": ["in", list(dates)], "material_request": ["is", "set"]}, fields=["parent", "material_request"])
	mr_dates = dict(frappe.get_all("Material Request", filters={"name": ["in", list({l.material_request for l in lines}) or [""]]}, fields=["name", "transaction_date"], as_list=True))
	req = {}
	for l in lines:
		d = mr_dates.get(l.material_request)
		if d:
			req[l.parent] = min(req.get(l.parent, d), d)
	to_order = [date_diff(dates[po], d) for po, d in req.items()]
	first = {}
	if _may("Purchase Receipt"):
		rows = frappe.get_all("Purchase Receipt Item", filters={"purchase_order": ["in", list(dates)], "docstatus": 1}, fields=["purchase_order", "parent"])
		pr_dates = dict(frappe.get_all("Purchase Receipt", filters={"name": ["in", list({r.parent for r in rows}) or [""]]}, fields=["name", "posting_date"], as_list=True))
		for r in rows:
			d = pr_dates.get(r.parent)
			if d and (r.purchase_order not in first or d < first[r.purchase_order]):
				first[r.purchase_order] = d
	to_receipt = [date_diff(d, dates[po]) for po, d in first.items()]
	avg = lambda xs: round(sum(xs) / len(xs), 1) if xs else None  # noqa: E731
	return {"requisition_to_order": avg(to_order), "order_to_receipt": avg(to_receipt), "orders": len(orders),
	        "with_requisition": len(to_order), "with_receipt": len(to_receipt)}


def _supplier_performance(po_filters, from_date, to_date):
	"""The period's largest suppliers with their scorecard and delivery record."""
	if not _may("Purchase Order"):
		return None
	orders = frappe.get_list("Purchase Order", filters=po_filters + [["Purchase Order", "docstatus", "=", 1], ["Purchase Order", "transaction_date", "between", [from_date, to_date]]],
	                         fields=["supplier", "supplier_name", "base_net_total"], limit_page_length=5000)
	totals = {}
	for o in orders:
		t = totals.setdefault(o.supplier, {"supplier": o.supplier, "supplier_name": o.supplier_name, "total": 0.0, "orders": 0})
		t["total"] += flt(o.base_net_total)
		t["orders"] += 1
	top = sorted(totals.values(), key=lambda r: -r["total"])[:8]
	scores = {}
	if top and _may("Supplier Scorecard"):
		scores = {r.name: r for r in frappe.get_list("Supplier Scorecard", filters={"name": ["in", [t["supplier"] for t in top]]}, fields=["name", "supplier_score", "status"])}
	for t in top:
		t["total"] = flt(t["total"], 2)
		sc = scores.get(t["supplier"])
		t["score"] = flt(sc.supplier_score) if sc else None
		t["standing"] = sc.status if sc else None
		t["delivery"] = _delivery_record(t["supplier"])
	return top


_SUMMARY_FIELDS = {
	"Material Request": ["title", "folt_estimated_total as amount", "folt_department as kind", "transaction_date as posting_date"],
	"Request for Quotation": ["transaction_date as posting_date"],
	"Supplier Quotation": ["supplier_name as title", "grand_total as amount", "transaction_date as posting_date"],
	"Procurement Committee Evaluation": ["recommended_supplier as title", "request_for_quotation as kind"],
	"Derogation Waiver Request": ["supplier as title", "estimated_value as amount"],
	"Purchase Order": ["supplier_name as title", "grand_total as amount", "transaction_date as posting_date", "folt_department as kind"],
	"Purchase Receipt": ["supplier_name as title", "grand_total as amount", "posting_date"],
	"Purchase Invoice": ["supplier_name as title", "grand_total as amount", "posting_date", "bill_no as kind"],
}


def _summarise(doctype, name):
	fields = [f for f in _SUMMARY_FIELDS.get(doctype, []) if frappe.get_meta(doctype).has_field(f.split(" ")[0])]
	return frappe.db.get_value(doctype, name, ["owner", "modified", "docstatus", *fields], as_dict=True) or {}


def _recent(limit=12):
	"""The latest changes on the buying documents the reader may open (Frappe's Version rows)."""
	doctypes = [dt for dt in CHAIN + ("Supplier",) if _may(dt)]
	if not doctypes:
		return []
	out = []
	for v in frappe.get_all("Version", filters={"ref_doctype": ["in", doctypes], "creation": [">=", add_days(nowdate(), -30)]},
	                        fields=["ref_doctype", "docname", "owner", "creation", "data"], order_by="creation desc", limit_page_length=limit * 4):
		if len(out) >= limit:
			break
		if not frappe.db.exists(v.ref_doctype, v.docname) or not frappe.has_permission(v.ref_doctype, "read", v.docname):
			continue
		out.append({"doctype": v.ref_doctype, "name": v.docname, "user": v.owner, "when": v.creation, "detail": frappe.utils.strip_html(_common.version_summary(v.data)).replace("\n", " ")[:300]})
	return out


# --- approvals --------------------------------------------------------------------------------


@frappe.whitelist()
def approvals():
	"""What waits on this person in the procurement workflows, and what they have sent and is waiting."""
	_only_procurement()
	return _common.approvals_view([dt for dt in CHAIN if _is_staff() or dt in REQUESTER_DOCTYPES], _summarise)


@frappe.whitelist(methods=["POST"])
def act_many(items, transition):
	"""The same workflow step on several documents at once (the approvals inbox's bulk Approve).

	Each document moves through apply_workflow as the person, exactly as one at a time, in its own
	savepoint: one that ERPNext refuses is reported and the others still move. A turn-down is never
	done in bulk -- each needs the reason for that document (workflow_access).
	"""
	from frappe.model.workflow import apply_workflow, get_workflow

	from folt_customizations.workflow_access import is_turn_down

	_only_procurement()
	rows = _json(items, []) or []
	if not isinstance(rows, list) or not rows or len(rows) > 50:
		frappe.throw(_("Choose between 1 and 50 documents."))
	done, failed = [], []
	for row in rows:
		doctype, name = (row or {}).get("doctype"), (row or {}).get("name")
		if doctype not in CHAIN:
			failed.append({"doctype": doctype, "name": name, "message": _("Not a procurement workflow document.")})
			continue
		_doctype(doctype)
		frappe.db.savepoint("act_many")
		try:
			doc = frappe.get_doc(doctype, name)
			wf = get_workflow(doctype)
			move = next((t for t in wf.transitions if t.state == doc.get(wf.workflow_state_field) and t.action == transition), None)
			if move and is_turn_down(wf, move.state, move.next_state):
				frappe.throw(_("{0} needs a reason for this document. Open it to do it.").format(transition))
			apply_workflow(doc, transition)
			done.append({"doctype": doctype, "name": name})
		except Exception as e:  # noqa: BLE001 -- reported per document; the rest still move
			frappe.db.rollback(save_point="act_many")
			frappe.clear_last_message()
			failed.append({"doctype": doctype, "name": name, "message": frappe.utils.strip_html(str(e)) or type(e).__name__})
	return {"done": done, "failed": failed}


# --- the chain a document is part of ----------------------------------------------------------

# (child doctype, link field, the doctype it names, the parent doctype): one line naming another
# document. Read in both directions, they join the buying chain from wherever one stands on it.
_LINKS = (
	("Request for Quotation Item", "material_request", "Material Request", "Request for Quotation"),
	("Supplier Quotation Item", "material_request", "Material Request", "Supplier Quotation"),
	("Supplier Quotation Item", "request_for_quotation", "Request for Quotation", "Supplier Quotation"),
	("Purchase Order Item", "material_request", "Material Request", "Purchase Order"),
	("Purchase Order Item", "supplier_quotation", "Supplier Quotation", "Purchase Order"),
	("Purchase Receipt Item", "purchase_order", "Purchase Order", "Purchase Receipt"),
	("Purchase Receipt Item", "material_request", "Material Request", "Purchase Receipt"),
	("Purchase Invoice Item", "purchase_order", "Purchase Order", "Purchase Invoice"),
	("Purchase Invoice Item", "purchase_receipt", "Purchase Receipt", "Purchase Invoice"),
	("Payment Entry Reference", "reference_name", "Purchase Invoice", "Payment Entry"),
)
# Parent-level links: (doctype, field, the doctype it names).
_HEAD_LINKS = (
	("Procurement Committee Evaluation", "request_for_quotation", "Request for Quotation"),
	("Derogation Waiver Request", "supplier_quotation", "Supplier Quotation"),
	("Purchase Order", "folt_committee_evaluation", "Procurement Committee Evaluation"),
	("Purchase Order", "folt_waiver_request", "Derogation Waiver Request"),
)
_FLOW_ORDER = ("Material Request", "Request for Quotation", "Supplier Quotation", "Procurement Committee Evaluation",
               "Derogation Waiver Request", "Purchase Order", "Purchase Receipt", "Purchase Invoice", "Payment Entry")
_FLOW_FIELDS = {
	"Material Request": ["transaction_date as date", "folt_estimated_total as amount"],
	"Request for Quotation": ["transaction_date as date"],
	"Supplier Quotation": ["transaction_date as date", "supplier_name as party", "grand_total as amount"],
	"Procurement Committee Evaluation": ["recommended_supplier as party"],
	"Derogation Waiver Request": ["supplier as party", "estimated_value as amount"],
	"Purchase Order": ["transaction_date as date", "supplier_name as party", "grand_total as amount"],
	"Purchase Receipt": ["posting_date as date", "supplier_name as party", "grand_total as amount"],
	"Purchase Invoice": ["posting_date as date", "supplier_name as party", "grand_total as amount"],
	"Payment Entry": ["posting_date as date", "party_name as party", "paid_amount as amount"],
}
_FLOW_CAP = 50


def _chain_of(doctype, name):
	"""Every document joined to this one along the buying chain, by the lines and links that name
	each other. Names only, gathered without permission checks; the caller lists them as the
	reader may see them."""
	found = {dt: set() for dt in _FLOW_ORDER}
	found[doctype].add(name)
	frontier = [(doctype, name)]
	while frontier:
		dt, dn = frontier.pop()
		new = []
		for child, field, target, parent in _LINKS:
			if dt == target:
				extra = {"reference_doctype": "Purchase Invoice"} if child == "Payment Entry Reference" else {}
				for p in frappe.get_all(child, filters={field: dn, "parenttype": parent, **extra}, pluck="parent", distinct=True):
					new.append((parent, p))
			elif dt == parent:
				for v in frappe.get_all(child, filters={"parent": dn, "parenttype": parent, field: ["is", "set"]}, pluck=field, distinct=True):
					new.append((target, v))
		for owner, field, target in _HEAD_LINKS:
			if dt == target:
				for p in frappe.get_all(owner, filters={field: dn}, pluck="name"):
					new.append((owner, p))
			elif dt == owner and frappe.get_meta(owner).has_field(field):
				v = frappe.db.get_value(owner, dn, field)
				if v:
					new.append((target, v))
		for ndt, ndn in new:
			if ndt in found and ndn not in found[ndt] and len(found[ndt]) < _FLOW_CAP:
				found[ndt].add(ndn)
				frontier.append((ndt, ndn))
	return found


@frappe.whitelist()
def flow(doctype, name):
	"""The documents this one is part of a chain with -- its requisition, RFQs, quotations, award,
	orders, receipts, invoices and payments -- each as the reader may see it; and for a
	requisition, its lines with what has been ordered and received against each (ERPNext's own
	ordered_qty and received_qty)."""
	_doctype(doctype)
	if doctype not in _FLOW_ORDER:
		frappe.throw(_("{0} is not on the buying chain.").format(doctype))
	frappe.get_doc(doctype, name).check_permission("read")
	found = _chain_of(doctype, name)
	documents = []
	hidden = 0
	for dt in _FLOW_ORDER:
		names = found[dt]
		if not names:
			continue
		if not _may(dt):
			hidden += len(names)
			continue
		meta = frappe.get_meta(dt)
		fields = ["name", "docstatus"] + [f for f in ("status", "workflow_state") if meta.has_field(f)]
		fields += [f for f in _FLOW_FIELDS.get(dt, []) if meta.has_field(f.split(" ")[0])]
		rows = frappe.get_list(dt, filters={"name": ["in", list(names)]}, fields=fields, order_by="creation asc")
		hidden += len(names) - len(rows)
		for r in rows:
			documents.append({"doctype": dt, **r, "current": dt == doctype and r.name == name})
	lines = []
	if doctype == "Material Request":
		lines = frappe.get_all("Material Request Item", filters={"parent": name, "parenttype": "Material Request"},
		                       fields=["item_code", "item_name", "qty", "uom", "stock_qty", "ordered_qty", "received_qty", "folt_estimated_rate", "folt_estimated_amount", "schedule_date"],
		                       order_by="idx asc")
	from folt_customizations import procurement_chain

	doc = frappe.get_doc(doctype, name)
	return {"documents": documents, "hidden": hidden, "lines": lines,
	        "note": procurement_chain.route_note(doc) if doctype in procurement_chain.HANDOFFS else None,
	        "handoffs": [_plain_handoff(h) for h in procurement_chain.handoffs_for(doc)]}


def _plain_handoff(h):
	return {"label": h["label"], "target": h["target"], "description": h["description"], "ready": bool(h["ready"]),
	        "ready_at": h["ready_at"], "existing": list(h["existing"] or []),
	        # Whether the reader may make it at all: the routes belong to different people (a buyer
	        # raises the evaluation, the Operations Support Officer the waiver).
	        "may_create": bool(frappe.has_permission(h["target"], "create"))}


@frappe.whitelist(methods=["POST"])
def handoff(doctype, name, target):
	"""The next document on the procurement chain, made by procurement_chain's own makers -- a
	committee evaluation or a waiver from a bid, the purchase order from an approved evaluation or
	waiver. Only the routes procurement_chain declares; each keeps its own rules (ready state,
	one per authority, what is copied)."""
	from folt_customizations import procurement_chain

	_doctype(doctype)
	if not _is_staff():
		frappe.throw(_("Only the procurement roles move a document along the chain."), frappe.PermissionError)
	row = next((h for h in procurement_chain.HANDOFFS.get(doctype, []) if h.target == target), None)
	if not row:
		frappe.throw(_("{0} does not lead to a {1}.").format(_(doctype), _(target)))
	frappe.get_doc(doctype, name).check_permission("read")
	frappe.has_permission(target, "create", throw=True)
	result = getattr(procurement_chain, row.method)(**{row.arg: name})
	new_name = result if isinstance(result, str) else (result or {}).get("name")
	return {"doctype": target, "name": new_name}


# --- comparing the bids against one RFQ --------------------------------------------------------


@frappe.whitelist()
def compare(name):
	"""The bids against one RFQ side by side: per line the unit price and lead time each supplier
	quoted, per bid its totals, taxes, validity, payment terms and how much of the RFQ it answers,
	per supplier its invitation, qualification and scorecard. Lowest figures are marked among the
	submitted bids, in company currency. Which bid wins is the committee's or the waiver's
	decision, never this one's: the existing evaluation and waivers are named, not made."""
	_doctype("Request for Quotation")
	if not _is_staff():
		frappe.throw(_("Comparing bids is for the procurement roles."), frappe.PermissionError)
	rfq = frappe.get_doc("Request for Quotation", name)
	rfq.check_permission("read")
	deadline = rfq.get("folt_quotation_deadline")
	from frappe.utils import get_datetime, now_datetime

	lines = [{"rfq_item": r.name, "item_code": r.item_code, "item_name": r.item_name, "qty": flt(r.qty), "uom": r.uom,
	          "schedule_date": str(r.schedule_date) if r.schedule_date else None} for r in rfq.items]
	by_code = {}
	for l in lines:
		by_code.setdefault(l["item_code"], l["rfq_item"])

	sq_names = frappe.get_all("Supplier Quotation Item", filters={"request_for_quotation": name, "parenttype": "Supplier Quotation"}, pluck="parent", distinct=True)
	quotations = []
	if sq_names and _may("Supplier Quotation"):
		heads = frappe.get_list("Supplier Quotation", filters={"name": ["in", sq_names], "docstatus": ["<", 2]},
		                        fields=["name", "supplier", "supplier_name", "docstatus", "status", "transaction_date", "valid_till", "currency",
		                                "net_total", "total_taxes_and_charges", "grand_total", "base_grand_total", "base_net_total", "payment_terms_template", "quotation_number"],
		                        order_by="base_grand_total asc")
		items = frappe.get_all("Supplier Quotation Item", filters={"parent": ["in", [h.name for h in heads] or [""]]},
		                       fields=["parent", "request_for_quotation_item", "item_code", "rate", "base_rate", "amount", "lead_time_days", "expected_delivery_date", "description"])
		per = {}
		for it in items:
			key = it.request_for_quotation_item or by_code.get(it.item_code)
			if key:
				per.setdefault(it.parent, {})[key] = {"rate": flt(it.rate), "base_rate": flt(it.base_rate), "amount": flt(it.amount),
				                                      "lead_time_days": it.lead_time_days, "expected_delivery_date": str(it.expected_delivery_date) if it.expected_delivery_date else None}
		for h in heads:
			got = per.get(h.name, {})
			quoted = sum(1 for l in lines if l["rfq_item"] in got and got[l["rfq_item"]]["rate"] > 0)
			leads = [v["lead_time_days"] for v in got.values() if v.get("lead_time_days")]
			quotations.append({**{k: (str(v) if k in ("transaction_date", "valid_till") and v else v) for k, v in h.items()},
			                   "lines": got, "quoted": quoted, "complete": quoted == len(lines),
			                   "lead_time_days": max(leads) if leads else None,
			                   "late": bool(deadline and h.transaction_date and getdate(h.transaction_date) > get_datetime(deadline).date()),
			                   "expired": bool(h.valid_till and getdate(h.valid_till) < getdate(nowdate()))})

	submitted = [q for q in quotations if q["docstatus"] == 1]
	lowest = {"total": min(submitted, key=lambda q: flt(q["base_grand_total"]))["name"] if submitted else None, "lines": {}}
	for l in lines:
		priced = [(q["lines"][l["rfq_item"]]["base_rate"], q["name"]) for q in submitted if l["rfq_item"] in q["lines"] and q["lines"][l["rfq_item"]]["rate"] > 0]
		lowest["lines"][l["rfq_item"]] = min(priced)[1] if priced else None

	invited = []
	scores = {}
	if _may("Supplier Scorecard"):
		scores = {r.name: r for r in frappe.get_list("Supplier Scorecard", filters={"name": ["in", [r.supplier for r in rfq.suppliers] or [""]]}, fields=["name", "supplier_score", "status"])}
	for r in rfq.suppliers:
		sup = frappe.db.get_value("Supplier", r.supplier, ["supplier_name", "folt_qualified_until", "disabled", "on_hold"], as_dict=True) or {}
		sc = scores.get(r.supplier)
		invited.append({"supplier": r.supplier, "supplier_name": sup.get("supplier_name") or r.supplier_name, "email_id": r.email_id,
		                "send_email": cint(r.send_email), "email_sent": cint(r.email_sent), "quote_status": r.quote_status,
		                "qualified_until": str(sup.get("folt_qualified_until")) if sup.get("folt_qualified_until") else None,
		                "qualified": (getdate(sup["folt_qualified_until"]) >= getdate(nowdate())) if sup.get("folt_qualified_until") else None,
		                "blocked": bool(sup.get("on_hold") or sup.get("disabled")),
		                "score": flt(sc.supplier_score) if sc else None, "standing": sc.status if sc else None,
		                "quotation": next((q["name"] for q in quotations if q["supplier"] == r.supplier), None)})

	evaluation = None
	if _may("Procurement Committee Evaluation"):
		ev = frappe.get_list("Procurement Committee Evaluation", filters={"request_for_quotation": name, "docstatus": ["<", 2]},
		                     fields=["name", "workflow_state", "recommended_supplier", "recommended_supplier_quotation"], limit_page_length=1)
		evaluation = ev[0] if ev else None
	waivers = []
	if _may("Derogation Waiver Request") and quotations:
		waivers = frappe.get_list("Derogation Waiver Request", filters={"supplier_quotation": ["in", [q["name"] for q in quotations]]},
		                          fields=["name", "supplier_quotation", "supplier", "workflow_state"])

	from folt_customizations import procurement_chain

	routes = {}
	for q in submitted:
		routes[q["name"]] = [_plain_handoff(h) for h in procurement_chain.handoffs_for(frappe.get_doc("Supplier Quotation", q["name"]))]

	return {
		"rfq": {"name": rfq.name, "docstatus": rfq.docstatus, "status": rfq.status, "transaction_date": str(rfq.transaction_date),
		        "schedule_date": str(rfq.schedule_date) if rfq.schedule_date else None, "deadline": str(deadline) if deadline else None,
		        "deadline_passed": bool(deadline and now_datetime() > get_datetime(deadline)), "company": rfq.company,
		        "currency": frappe.get_cached_value("Company", rfq.company, "default_currency")},
		"lines": lines,
		"suppliers": invited,
		"quotations": quotations,
		"lowest": lowest,
		"evaluation": evaluation,
		"waivers": waivers,
		"routes": routes,
	}

