"""What the app's allow-listed edges share (finance_portal, programs_portal, procurement_portal).

Each portal keeps its own allow-list and role floor; these are the pieces that do not depend on
either: reading a JSON argument, a list's columns from the meta, filters checked against the meta,
and a one-line summary of a Version.
"""

import csv as _csv
import io as _io
import re as _re

import frappe
from frappe import _
from frappe.utils import cint, nowdate

FILTER_OPS = {"=", "!=", ">", "<", ">=", "<=", "like", "not like", "in", "not in", "between", "is"}
STD_FIELDS = {"name", "owner", "creation", "modified", "modified_by", "docstatus", "amended_from"}



def _may_make(doc):
	"""The workflow moves this person may make on the document: Frappe's get_transitions (role and
	condition) less those its self-approval rule forbids them -- which get_transitions leaves in and
	apply_workflow refuses only when the button is pressed."""
	from frappe.model.workflow import get_transitions, has_approval_access

	return [t for t in get_transitions(doc) if has_approval_access(frappe.session.user, doc, t)]

def parse_json(value, default=None):
	if value in (None, ""):
		return default
	return frappe.parse_json(value) if isinstance(value, str) else value


def columns(doctype, wanted):
	meta = frappe.get_meta(doctype)
	out = []
	for fieldname in wanted:
		df = meta.get_field(fieldname)
		if df:
			out.append({"fieldname": fieldname, "label": _(df.label), "fieldtype": df.fieldtype, "options": df.options})
	return out


def filters(doctype, raw):
	meta = frappe.get_meta(doctype)
	out = []
	for row in parse_json(raw, []) or []:
		if not isinstance(row, (list, tuple)) or len(row) != 3:
			frappe.throw(_("A filter is [field, operator, value]."))
		field, op, value = row
		if op not in FILTER_OPS or (field not in STD_FIELDS and not meta.get_field(field)):
			frappe.throw(_("Cannot filter {0} by {1} {2}.").format(doctype, field, op))
		out.append([doctype, field, op, value])
	return out


def version_summary(data):
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


# --- documents ---------------------------------------------------------------------------------
#
# The document half of an allow-listed edge (finance_portal, procurement_portal). Each edge checks
# its own role floor and allow-list FIRST and then hands the doctype here; nothing below decides
# who may do what -- Frappe's document permissions and the doctype's workflow do.

# Never written from a form: the lifecycle fields belong to submit, the workflow and amend.
# Fields a person never sets through an edge. `owner` decides who an owner-only permission
# belongs to and whom the self-approval rule protects; `name` on an existing document would point the
# save at another record's row; the rest are Frappe's bookkeeping, or facts FoLT's own code records
# (a match approved despite its flags, a supplier's acknowledgement on the portal).
PROTECTED = (
	"docstatus", "doctype", "amended_from", "workflow_state", "folt_rejection_reason",
	"name", "owner", "creation", "modified_by",
	"folt_match_override_reason", "folt_supplier_acknowledged_on", "folt_supplier_acknowledged_by",
)


def mask(value):
	"""All but the last four characters, for an account number shown to somebody who only uses it."""
	value = str(value or "")
	return _re.sub(r"[0-9A-Za-z](?=.{4})", "•", value) if value else value


def record_view(doctype, name=None, masker=None):
	"""A document with what its screen shows: workflow moves for this person, ledger impact,
	payments against it, timeline, attachments and print formats. `masker(doctype, data)` may
	blank fields the reader should only see in part."""
	from frappe.desk.form.load import get_docinfo
	from frappe.model.workflow import get_workflow, get_workflow_name

	from folt_customizations.workflow_access import is_turn_down

	meta = frappe.get_meta(doctype)
	name = doctype if meta.issingle else name
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	doc.apply_fieldlevel_read_permissions()

	get_docinfo(doc)
	docinfo = frappe.response.pop("docinfo", {}) or {}

	data = doc.as_dict(convert_dates_to_str=True)
	if masker:
		masker(doctype, data)

	workflow = None
	workflow_name = get_workflow_name(doctype)
	if workflow_name:
		wf = get_workflow(doctype)
		state = doc.get(wf.workflow_state_field)
		moves = []
		if doc.docstatus < 2:
			for t in _may_make(doc):
				from folt_customizations.match import needs_reason as match_needs_reason

				moves.append({"action": t.action, "next_state": t.next_state,
				              "needs_reason": is_turn_down(wf, state, t.next_state) or match_needs_reason(doc, t.next_state)})
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


def meta_view(doctype):
	"""The fields of a doctype (and its child tables), for a form drawn from them."""

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


def list_view(doctype, wanted, search, q=None, raw_filters=None, order_by=None, start=0, page_length=20, extra_filters=None):
	"""One page of a list, as the person may see it, with its total."""
	meta = frappe.get_meta(doctype)
	if meta.issingle:
		frappe.throw(_("{0} is a single settings record, not a list.").format(doctype))
	cols = columns(doctype, wanted)
	fields = ["name", "docstatus", "modified"] + [c["fieldname"] for c in cols]
	for extra in ("status", "workflow_state", "company", meta.title_field):
		if extra and meta.get_field(extra) and extra not in fields:
			fields.append(extra)

	where = filters(doctype, raw_filters) + list(extra_filters or [])
	or_filters = [[doctype, f, "like", f"%{q}%"] for f in search if f == "name" or meta.get_field(f)] if q else None

	order = "modified desc"
	if order_by:
		field, _sp, direction = str(order_by).partition(" ")
		if (field in STD_FIELDS or meta.get_field(field)) and direction.lower() in ("", "asc", "desc"):
			order = f"{field} {direction.lower() or 'asc'}"

	page_length = min(max(cint(page_length) or 20, 1), 500)
	rows = frappe.get_list(doctype, fields=fields, filters=where, or_filters=or_filters, order_by=order,
	                       limit_start=cint(start), limit_page_length=page_length)
	total = frappe.get_list(doctype, fields=[{"COUNT": "*", "as": "total"}], filters=where, or_filters=or_filters)
	return {
		"columns": cols,
		"rows": rows,
		"total": total[0].total if total else 0,
		"title_field": meta.title_field,
		"submittable": bool(meta.is_submittable),
	}


def complete_item_rows(doc):
	"""What the Desk's form fills in the browser as an item is picked, and a save from the app does not.

	The Desk gets a line's stock UOM, UOM and conversion factor from get_item_details when the item
	is chosen; the server-side controllers of most buying documents set a missing factor, but
	Request for Quotation's does not, so a line saved from the app came back "UOM Conversion Factor
	is mandatory". Any child table with an item and a conversion factor is completed here, only
	where a value is missing, so nothing the person chose is overwritten. The factor is ERPNext's
	own (the item's UOM table, its template's, then the global UOM conversions), 1 when none is set,
	which is what the buying controllers fall back to as well.
	"""
	from erpnext.stock.get_item_details import get_conversion_factor

	for df in doc.meta.get_table_fields():
		child = frappe.get_meta(df.options)
		if not (child.has_field("item_code") and child.has_field("conversion_factor")):
			continue
		for row in doc.get(df.fieldname) or []:
			if not row.get("item_code"):
				continue
			item = frappe.get_cached_value("Item", row.item_code, ["stock_uom", "purchase_uom", "item_name", "description"], as_dict=True)
			if not item:
				continue
			if child.has_field("stock_uom") and not row.get("stock_uom"):
				row.stock_uom = item.stock_uom
			if child.has_field("uom") and not row.get("uom"):
				row.uom = item.purchase_uom or item.stock_uom
			if not row.get("conversion_factor"):
				row.conversion_factor = get_conversion_factor(row.item_code, row.get("uom") or item.stock_uom).get("conversion_factor") or 1
			if child.has_field("item_name") and not row.get("item_name"):
				row.item_name = item.item_name
			if child.has_field("description") and not row.get("description"):
				row.description = item.description or item.item_name
			if child.has_field("schedule_date") and not row.get("schedule_date") and doc.get("schedule_date"):
				row.schedule_date = doc.schedule_date


def save_doc(doctype, doc, name=None, protected=PROTECTED):
	"""Save a draft (new or existing), or a single settings record. Never submits.

	For an existing document only the fields sent are changed, and a child table sent is the whole
	table. `modified` travels with an existing document, so somebody else's newer save is refused
	rather than overwritten.
	"""
	values = parse_json(doc, {}) or {}
	for field in protected:
		values.pop(field, None)
	m = frappe.get_meta(doctype)

	if m.issingle:
		target = frappe.get_doc(doctype)
		target.update(values)
		target.save()
	elif name:
		target = frappe.get_doc(doctype, name)
		# Child rows sent back keep their own names, but each must already be this document's:
		# a row named after another document's row would be written over that row.
		for field in m.get_table_fields():
			rows = values.get(field.fieldname)
			if isinstance(rows, list):
				mine = {r.name for r in target.get(field.fieldname) or []}
				for row in rows:
					if isinstance(row, dict) and row.get("name") and row["name"] not in mine:
						row.pop("name")
					if isinstance(row, dict):
						for key in ("parent", "parenttype", "parentfield", "owner", "docstatus"):
							row.pop(key, None)
		if target.docstatus == 2:
			frappe.throw(_("{0} is cancelled. Amend it to make a new version.").format(name))
		target.update(values)
		complete_item_rows(target)
		template_taxes(target, m)
		target.save()
	else:
		values.pop("name", None)
		# A doctype named by prompt (Employee Grade, Salary Structure, Payroll Period, Tax Withholding
		# Category...) takes the name the person typed, sent as Frappe's own `__newname`; `name` is
		# never taken from a form (PROTECTED). Anything else ignores it.
		new_name = str(values.pop("__newname", "") or "").strip()
		target = frappe.get_doc({"doctype": doctype, **values})
		if (m.autoname or "").lower() == "prompt":
			if not new_name:
				frappe.throw(_("Give the new {0} a name.").format(_(doctype)))
			target.name = new_name
		complete_item_rows(target)
		template_taxes(target, m)
		target.insert()
	return target


def template_taxes(doc, m):
	"""A tax template chosen with no tax rows gets the template's rows, as the Desk's form does.

	ERPNext's controllers do not copy a template's rows on save; the Desk does it in the browser
	when the template is picked. Doing it here keeps a document saved from the app from quietly
	going out without its VAT.
	"""
	df = m.get_field("taxes_and_charges")
	if not df or not doc.get("taxes_and_charges") or doc.get("taxes"):
		return
	from erpnext.controllers.accounts_controller import get_taxes_and_charges

	for row in get_taxes_and_charges(df.options, doc.taxes_and_charges) or []:
		doc.append("taxes", row)


def preview_doc(doctype, doc, methods, after=None):
	"""ERPNext's own defaults and totals for an unsaved draft. Writes nothing.

	Runs the controller's own setters on an unsaved copy, so a total in the form is ERPNext's
	total, not one the browser added up.
	"""
	if not (frappe.has_permission(doctype, "create") or frappe.has_permission(doctype, "write")):
		frappe.throw(_("You cannot prepare a {0}.").format(_(doctype)), frappe.PermissionError)
	values = parse_json(doc, {}) or {}
	values.pop("docstatus", None)
	draft = frappe.get_doc({"doctype": doctype, **values})
	draft.docstatus = 0
	complete_item_rows(draft)
	for method in methods:
		fn = getattr(draft, method, None)
		if not callable(fn):
			continue
		try:
			fn()
		except Exception:  # noqa: BLE001
			# A half-filled form is normal while somebody types. The preview is advice; the save
			# validates for real and says what is missing.
			frappe.clear_last_message()
	if after:
		try:
			after(draft)
		except frappe.PermissionError:
			raise  # a refusal is not a half-filled form
		except Exception:  # noqa: BLE001 -- advice, as above
			frappe.clear_last_message()
	return draft.as_dict(convert_dates_to_str=True)


def act_on(doctype, name, action, transition=None, reason=None):
	"""Move a document: a workflow step, or ERPNext's submit / cancel / amend / delete.

	A workflow step goes through apply_workflow exactly as the Desk's button does, with the reason
	for a turn-down parked first (workflow_access). A document with a workflow is posted and
	reversed only by its workflow's moves.
	"""
	from frappe.model.workflow import apply_workflow, get_workflow_name

	from folt_customizations.workflow_access import hold_rejection_reason

	doc = frappe.get_doc(doctype, name)
	if action == "transition":
		if not transition:
			frappe.throw(_("Which step?"))
		if reason and reason.strip():
			hold_rejection_reason(doctype, name, reason.strip())
		apply_workflow(doc, transition)
		return {"name": doc.name}
	if action in ("submit", "cancel") and get_workflow_name(doctype):
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
		if get_workflow_name(doctype):
			# An amendment starts its approval over: at the workflow's first state, not the
			# cancelled document's (which Frappe would read as a transition out of Draft).
			from frappe.model.workflow import get_workflow

			wf = get_workflow(doctype)
			new.set(wf.workflow_state_field, wf.states[0].state)
			if new.meta.has_field("folt_rejection_reason"):
				new.folt_rejection_reason = None
		new.insert()
		return {"name": new.name}
	if action == "delete":
		frappe.delete_doc(doctype, name)
		return {"name": None}
	frappe.throw(_("Unknown action {0}.").format(action))


def attach_file(doctype, docname):
	"""Frappe's own upload_file, private, onto a document; Frappe checks the write right."""
	frappe.form_dict.update({"doctype": doctype, "docname": docname, "is_private": 1})
	from frappe.handler import upload_file

	f = upload_file()
	return {"name": f.name, "file_name": f.file_name, "file_url": f.file_url, "is_private": f.is_private}


def copy_view(doctype, name):
	"""An unsaved copy of a document, to start a new one from (Duplicate). Writes nothing."""
	source = frappe.get_doc(doctype, name)
	source.check_permission("read")
	new = frappe.copy_doc(source)
	new.docstatus = 0
	return new.as_dict(convert_dates_to_str=True)


def link_search(doctype, txt="", raw_filters=None, page_length=20):
	"""Frappe's own link search (the edge refuses the doctypes it never searches first)."""
	from frappe.desk.search import search_link

	return search_link(doctype, txt or "", filters=parse_json(raw_filters), page_length=min(cint(page_length) or 20, 50))


def call_allowed(method, args, allowed, refusal):
	if method not in allowed:
		frappe.throw(refusal.format(method), frappe.PermissionError)
	return frappe.call(method, **(parse_json(args, {}) or {}))


# --- reports -----------------------------------------------------------------------------------


def run_report(report, raw_filters):
	"""A native report, run as the person asking, exactly as the Desk runs it."""
	from frappe.desk.query_report import run

	return run(report, filters=parse_json(raw_filters, {}) or {}, ignore_prepared_report=True)


def report_view(out):
	return {k: out.get(k) for k in ("columns", "result", "message", "chart", "report_summary", "add_total_row", "skip_total_row")}


def _cell(value):
	if isinstance(value, str) and len(value) > 1 and value[0] == value[-1] == "'":
		return value[1:-1]  # the reports quote their label rows ('Total', 'Opening')
	return value


def export_report(report, out, file_format="xlsx"):
	"""A report's own rows and columns as a spreadsheet, nothing recomputed."""
	cols = [c if isinstance(c, dict) else {"label": c, "fieldname": c} for c in out.get("columns") or []]
	cols = [c for c in cols if not c.get("hidden")]
	table = [[_(c.get("label") or c.get("fieldname")) for c in cols]]
	for row in out.get("result") or []:
		if isinstance(row, dict):
			indent = "    " * cint(row.get("indent") or 0)
			cells = [_cell(row.get(c.get("fieldname"))) for c in cols]
			if cells and isinstance(cells[0], str):
				cells[0] = indent + cells[0]
		else:
			cells = [_cell(v) for v in row[: len(cols)]]
		table.append(["" if v is None else v for v in cells])

	filename = f"{report} {nowdate()}"
	if file_format == "csv":
		buf = _io.StringIO()
		_csv.writer(buf).writerows(table)
		frappe.response.filename = f"{filename}.csv"
		frappe.response.filecontent = buf.getvalue()
	else:
		from frappe.utils.xlsxutils import make_xlsx

		frappe.response.filename = f"{filename}.xlsx"
		frappe.response.filecontent = make_xlsx(table, report[:31]).getvalue()
	frappe.response.type = "binary"


# --- approvals and audit -----------------------------------------------------------------------


def approvals_view(doctypes, summarise):
	"""What waits on this person in the given workflows, and what they have sent that is waiting."""
	from frappe.model.workflow import get_workflow_name

	governed = [dt for dt in doctypes if get_workflow_name(dt) and frappe.has_permission(dt, "read")]
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
		moves = [t.action for t in _may_make(doc)] if doc.docstatus < 2 else []
		if not moves:
			continue
		waiting.append({"doctype": a.reference_doctype, "name": a.reference_name, "state": a.workflow_state, "since": a.creation,
		                "moves": moves, **summarise(a.reference_doctype, a.reference_name)})

	mine = []
	for dt in governed:
		for row in frappe.get_list(dt, filters={"owner": frappe.session.user, "docstatus": 0}, fields=["name", "workflow_state", "modified"],
		                           order_by="modified desc", limit_page_length=50):
			mine.append({"doctype": dt, "name": row.name, "state": row.workflow_state, "since": row.modified, **summarise(dt, row.name)})
	mine.sort(key=lambda r: str(r["since"]), reverse=True)
	return {"waiting": waiting, "mine": mine, "workflows": {dt: get_workflow_name(dt) for dt in governed}}
