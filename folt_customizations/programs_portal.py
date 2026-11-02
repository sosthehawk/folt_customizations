"""The FoLT app's Programs master data, server half (folt-workflow-ui /programs/master-data; BFF
package `programs`; ADR-006).

Master data in the app is the master doctypes themselves, reached without the Desk. Nothing here
keeps a copy of a record or decides a state: every record is saved as the person asking, every
lifecycle move is the master workflow's own transition (masterdata.py), every count is a
permission-scoped query. What this module adds is the edge:

*Which doctypes.* DOCTYPES is the allow-list; the BFF in front holds the same list as a spec
enum. ERPNext records Programs reuses rather than copies -- Department -- are listed read-only:
they are kept where they belong (HR) and shown here with the programmes that use them.

*Who.* Every call requires one of READERS. Frappe then checks the doctype and document
permission itself, so the role gate is a floor, never the whole decision.

*Composition.* A record comes back with what its screen needs in one call: its lifecycle moves
for this person, where it is used, its place in a tree, its history.
"""

import csv
import io

import frappe
from frappe import _
from frappe.model.workflow import apply_workflow, get_transitions, get_workflow, get_workflow_name, has_approval_access
from frappe.utils import cint, getdate, nowdate

from folt_customizations import masterdata
from folt_customizations.portal_common import STD_FIELDS, columns, filters, parse_json, version_summary
from folt_customizations.workflow_access import hold_rejection_reason, is_turn_down

READERS = (
	"Master Data Administrator", "Master Data Approver", "Program Manager", "Program Officer", "M&E Officer",
	"Head of Programs", "Auditor", "System Manager", "Accounts Manager", "Accounts User", "Finance Officer",
	"Head of Finance", "Executive Director",
)

# doctype -> (list columns, searched fields, date field). The lifecycle state and the code are
# added to every master's list; read-only doctypes are ERPNext's own, shown, not edited here.
DOCTYPES = {
	"FoLT Program": (["program_code", "category", "program_type", "program_status", "department", "start_date", "end_date"], ["name", "program_code"], "start_date"),
	"FoLT Program Category": (["category_code", "description"], ["name", "category_code"], None),
	"FoLT Program Type": (["type_code", "description"], ["name", "type_code"], None),
	"FoLT Program Area": (["area_code", "parent_folt_program_area", "is_group"], ["name", "area_code"], None),
	"FoLT Program Status": (["status_code", "counts_as_active", "indicator"], ["name", "status_code"], None),
	"FoLT Location": (["location_code", "location_level", "parent_folt_location", "is_group"], ["name", "location_code"], None),
	"FoLT Donor": (["donor_code"], ["name", "donor_code"], None),
	"FoLT Grant Project": (["grant_code", "donor", "start_date", "end_date", "grant_currency"], ["name", "grant_code", "donor"], "start_date"),
	"FoLT Partner": (["partner_code", "partner_type", "registration_number"], ["name", "partner_code"], None),
	"FoLT Beneficiary Category": (["category_code", "classification", "parent_folt_beneficiary_category", "is_group"], ["name", "category_code"], None),
	"FoLT Objective": (["objective_name", "strategic_alignment"], ["name", "objective_name"], None),
	"FoLT Outcome": (["outcome_name", "objective"], ["name", "outcome_name"], None),
	"FoLT Output": (["output_name", "outcome", "expected_quantity", "uom"], ["name", "output_name"], None),
	"FoLT Indicator": (["indicator_name", "indicator_type", "output", "target_value", "frequency"], ["name", "indicator_name"], None),
	"FoLT Activity Template": (["activity_name", "activity_category", "department", "standard_duration_days"], ["name", "activity_name"], None),
	"FoLT Budget Line": (["budget_line_name", "classification", "parent_folt_budget_line", "account"], ["name", "budget_line_name"], None),
	"FoLT Funding Allocation": (["allocation_title", "program", "funding_source", "fiscal_year", "amount", "currency"], ["name", "allocation_title"], "creation"),
	"FoLT Reporting Period": (["period_name", "frequency", "program", "donor", "deadline", "reporting_status"], ["name", "period_name"], "deadline"),
	"Department": (["department_name", "parent_department", "company", "is_group", "disabled"], ["name", "department_name"], None),
	"FoLT Master Data Settings": ([], [], None),
}
READ_ONLY = {"Department"}

# What a master form's Link fields may search: the masters, and the ERPNext records they point at.
LINK_TARGETS = set(DOCTYPES) | {
	"Company", "Department", "Employee", "Cost Center", "Project", "Currency", "UOM", "Account", "Supplier",
	"Customer", "Fiscal Year", "Project Template", "Role", "Workflow State", "Budget",
}

# Fields that only the lifecycle, the tree or the server set.
# `name`, `owner` and Frappe's bookkeeping are never the client's to set (see portal_common.PROTECTED).
_PROTECTED = {"docstatus", "doctype", "workflow_state", "is_active", "folt_rejection_reason", "lft", "rgt", "old_parent",
              "name", "owner", "creation", "modified_by"}


# --- the edge -------------------------------------------------------------------------------


def _only_readers():
	if not set(frappe.get_roles()) & set(READERS):
		frappe.throw(_("Programs master data is open to the programme, finance and audit roles only."), frappe.PermissionError)


def _doctype(doctype: str) -> str:
	if doctype not in DOCTYPES:
		frappe.throw(_("{0} is not part of Programs master data.").format(doctype), frappe.PermissionError)
	return doctype


def _writable(doctype: str) -> str:
	_doctype(doctype)
	if doctype in READ_ONLY:
		frappe.throw(_("{0} is kept elsewhere; it is shown here, not changed.").format(_(doctype)), frappe.PermissionError)
	return doctype


def _title_field(doctype):
	return masterdata.MASTERS[doctype][2] if doctype in masterdata.MASTERS else frappe.get_meta(doctype).title_field


# --- context ----------------------------------------------------------------------------------


@frappe.whitelist()
def context():
	"""What every master-data screen needs before it asks anything else."""
	_only_readers()
	roles = set(frappe.get_roles())
	capabilities = {
		dt: {ptype: bool(frappe.has_permission(dt, ptype)) and (ptype in ("read", "export") or dt not in READ_ONLY) for ptype in ("read", "create", "write", "delete", "export")}
		for dt in DOCTYPES
	}
	return {
		"capabilities": capabilities,
		"workflows": {dt: get_workflow_name(dt) for dt in masterdata.MASTERS if get_workflow_name(dt)},
		"states": list(masterdata.STATES),
		"roles": {
			"preparer": bool(roles & set(masterdata.PREPARERS)) or "System Manager" in roles,
			"approver": bool(roles & set(masterdata.APPROVERS)) or "System Manager" in roles,
			"steward": bool(roles & set(masterdata.STEWARDS)) or "System Manager" in roles,
			"administrator": bool(roles & {"Master Data Administrator", "System Manager"}),
		},
		# The company names a programme may belong to; programme roles hold no Company permission.
		"companies": frappe.get_all("Company", pluck="name", order_by="name"),
	}


# --- dashboard ----------------------------------------------------------------------------------


def _count(doctype, flt=None):
	if not frappe.has_permission(doctype, "read"):
		return None
	return frappe.get_list(doctype, fields=[{"COUNT": "*", "as": "n"}], filters=flt or {})[0].n


@frappe.whitelist()
def dashboard():
	"""Counts by lifecycle state for every master, the programmes by status, what awaits review,
	and what changed lately -- each a permission-scoped query, nothing remembered."""
	_only_readers()
	masters = []
	for dt in masterdata.MASTERS:
		if not frappe.has_permission(dt, "read"):
			continue
		by_state = {
			r.workflow_state or masterdata.DRAFT: r.n
			for r in frappe.get_list(dt, fields=["workflow_state", {"COUNT": "*", "as": "n"}], group_by="workflow_state")
		}
		masters.append({"doctype": dt, "total": sum(by_state.values()), "by_state": by_state})

	programs = None
	if frappe.has_permission("FoLT Program", "read"):
		active_statuses = frappe.get_all("FoLT Program Status", filters={"counts_as_active": 1}, pluck="name")
		programs = {
			"total": _count("FoLT Program"),
			"active": _count("FoLT Program", {"program_status": ["in", active_statuses or [""]], "is_active": 1}),
			"by_status": frappe.get_list("FoLT Program", fields=["program_status", {"COUNT": "*", "as": "n"}], group_by="program_status"),
			"by_category": frappe.get_list("FoLT Program", fields=["category", {"COUNT": "*", "as": "n"}], group_by="category"),
		}

	locations = None
	if frappe.has_permission("FoLT Location", "read"):
		locations = frappe.get_list("FoLT Location", fields=["location_level", {"COUNT": "*", "as": "n"}], filters={"is_active": 1}, group_by="location_level")

	pending, recent = [], []
	for dt in masterdata.MASTERS:
		if not frappe.has_permission(dt, "read"):
			continue
		title = _title_field(dt)
		for r in frappe.get_list(dt, filters={"workflow_state": masterdata.PENDING}, fields=["name", title, "owner", "modified"], order_by="modified asc", limit_page_length=50):
			pending.append({"doctype": dt, "name": r.name, "title": r.get(title), "owner": r.owner, "since": r.modified})
		for r in frappe.get_list(dt, fields=["name", title, "workflow_state", "modified", "modified_by", "creation"], order_by="modified desc", limit_page_length=10):
			recent.append({"doctype": dt, "name": r.name, "title": r.get(title), "state": r.workflow_state, "modified": r.modified,
			               "modified_by": r.modified_by, "created": r.creation == r.modified})
	pending.sort(key=lambda r: str(r["since"]))
	recent.sort(key=lambda r: str(r["modified"]), reverse=True)
	return {"masters": masters, "programs": programs, "locations": locations, "pending": pending, "recent": recent[:15]}


# --- lists and records ------------------------------------------------------------------------


@frappe.whitelist()
def records(doctype, q=None, filters=None, order_by=None, start=0, page_length=20):
	"""One page of a master list, as the person may see it, with its total."""
	_only_readers()
	_doctype(doctype)
	meta = frappe.get_meta(doctype)
	if meta.issingle:
		frappe.throw(_("{0} is a single settings record, not a list.").format(doctype))
	wanted, search, _date = DOCTYPES[doctype]
	cols = columns(doctype, wanted)
	fields = ["name", "modified"] + [c["fieldname"] for c in cols]
	for extra in ("workflow_state", "is_active", meta.title_field):
		if extra and meta.get_field(extra) and extra not in fields:
			fields.append(extra)

	where = _filters_for(doctype, filters)
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
	link_cols = [c for c in cols if c["fieldtype"] == "Link"]
	return {
		"link_titles": link_titles((c["options"], r.get(c["fieldname"])) for r in rows for c in link_cols),
		"columns": cols,
		"rows": rows,
		"total": total[0].total if total else 0,
		"title_field": meta.title_field,
		"is_tree": bool(meta.is_tree),
		"read_only": doctype in READ_ONLY,
	}


def _titled(doctype) -> str | None:
	"""The title field of a master named by its code (results framework, allocations, periods)."""
	if doctype not in masterdata.MASTERS:
		return None
	meta = frappe.get_meta(doctype)
	return meta.title_field if meta.show_title_field_in_link and meta.title_field else None


def link_titles(pairs) -> dict:
	"""{doctype: {name: title}} for the code-named masters among (doctype, name) pairs, so a screen
	shows "Children learn" where the record says OBJ-0003. Titles of master records only."""
	wanted: dict[str, set] = {}
	for doctype, name in pairs:
		if name and _titled(doctype):
			wanted.setdefault(doctype, set()).add(name)
	out = {}
	for doctype, names in wanted.items():
		field = _titled(doctype)
		out[doctype] = {r.name: r.get(field) for r in frappe.get_all(doctype, filters={"name": ["in", list(names)]}, fields=["name", field])}
	return out


def _link_pairs(meta, row):
	for df in meta.get_link_fields():
		if row.get(df.fieldname):
			yield df.options, row.get(df.fieldname)


def _doc_link_pairs(doc):
	yield from _link_pairs(doc.meta, doc)
	for df in doc.meta.get_table_fields():
		child = frappe.get_meta(df.options)
		for row in doc.get(df.fieldname) or []:
			yield from _link_pairs(child, row)


def _filters_for(doctype, raw):
	"""The list's filters, plus one the meta cannot express: a Table MultiSelect value.

	["locations", "has", "Lodwar"] keeps the records whose `locations` table names Lodwar.
	"""
	plain, has = [], []
	for row in parse_json(raw, []) or []:
		if isinstance(row, (list, tuple)) and len(row) == 3 and row[1] == "has":
			has.append(row)
		else:
			plain.append(row)
	out = filters(doctype, plain)
	meta = frappe.get_meta(doctype)
	for field, _op, value in has:
		df = meta.get_field(field)
		if not df or df.fieldtype != "Table MultiSelect":
			frappe.throw(_("Cannot filter {0} by {1}.").format(doctype, field))
		child = frappe.get_meta(df.options)
		link = next((c.fieldname for c in child.get_link_fields()), None)
		parents = frappe.get_all(df.options, filters={"parenttype": doctype, "parentfield": field, link: value}, pluck="parent", distinct=True)
		out.append([doctype, "name", "in", parents or [""]])
	return out


@frappe.whitelist()
def record(doctype, name=None):
	"""A master record with its lifecycle moves, where it is used, its tree and its history."""
	_only_readers()
	_doctype(doctype)
	meta = frappe.get_meta(doctype)
	name = doctype if meta.issingle else name
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	doc.apply_fieldlevel_read_permissions()

	from frappe.desk.form.load import get_docinfo

	get_docinfo(doc)
	docinfo = frappe.response.pop("docinfo", {}) or {}

	workflow = None
	if get_workflow_name(doctype):
		wf = get_workflow(doctype)
		state = doc.get(wf.workflow_state_field)
		# get_transitions lists by role only; apply_workflow also refuses a self-approval, so a move
		# the person would be refused is not offered.
		moves = [
			{"action": t.action, "next_state": t.next_state, "needs_reason": is_turn_down(wf, state, t.next_state)}
			for t in get_transitions(doc)
			if has_approval_access(frappe.session.user, doc, t)
		]
		workflow = {"name": wf.name, "state": state, "transitions": moves}

	perms = docinfo.get("permissions") or {}
	if doctype in READ_ONLY:
		perms = {"read": perms.get("read")}
	elif workflow and perms.get("write"):
		# Who may change a record at its state is the workflow's allow_edit
		# (workflow_access.enforce_state_custodian refuses anybody else's save), so Edit is offered
		# only to them.
		roles = set(frappe.get_roles())
		perms["write"] = frappe.session.user == "Administrator" or any(
			row.allow_edit in roles for row in get_workflow(doctype).states if row.state == workflow["state"]
		)
	used = [] if meta.issingle else usage(doctype, doc.name)
	return {
		"doctype": doctype,
		"name": doc.name,
		"title": doc.get(meta.title_field) if meta.title_field else None,
		"link_titles": link_titles([*_doc_link_pairs(doc), *((u["doctype"], n) for u in used for n in u["sample"])]),
		"doc": doc.as_dict(convert_dates_to_str=True),
		"permissions": {k: bool(perms.get(k)) for k in ("read", "write", "create", "delete")},
		"deletable": bool(perms.get("delete")) and doc.get("workflow_state") in (None, "", masterdata.DRAFT, masterdata.REJECTED),
		"workflow": workflow,
		"tree": _tree_context(doc) if meta.is_tree else None,
		"usage": used,
		"timeline": {
			"versions": [{"owner": v.get("owner"), "creation": v.get("creation"), "detail": _change(v.get("data"))} for v in docinfo.get("versions", [])],
			"comments": [{"comment_type": c.get("comment_type"), "content": c.get("content"), "owner": c.get("owner"), "creation": c.get("creation")} for c in docinfo.get("comments", [])],
			"workflow_logs": [{"content": c.get("content"), "owner": c.get("owner"), "creation": c.get("creation")} for c in docinfo.get("workflow_logs", [])],
		},
		"attachments": [{"name": a.get("name"), "file_name": a.get("file_name"), "file_url": a.get("file_url"), "is_private": a.get("is_private")} for a in docinfo.get("attachments", [])],
	}


# What the tree and the lifecycle keep for themselves: not news in a record's history.
_QUIET = {"lft", "rgt", "old_parent", "is_active", "modified"}


def _change(data):
	d = parse_json(data, {}) or {}
	if isinstance(d, dict) and d.get("changed"):
		d = {**d, "changed": [c for c in d["changed"] if c and c[0] not in _QUIET]}
	return version_summary(frappe.as_json(d))


def _tree_context(doc):
	parent_field = doc.meta.nsm_parent_field or f"parent_{frappe.scrub(doc.doctype)}"
	title = _title_field(doc.doctype)
	ancestors = frappe.get_list(doc.doctype, filters={"lft": ["<", doc.lft], "rgt": [">", doc.rgt]}, fields=["name", title], order_by="lft asc") if doc.lft else []
	children = frappe.get_list(doc.doctype, filters={parent_field: doc.name}, fields=["name", title, "is_group", "workflow_state"], order_by=f"{title} asc", limit_page_length=500)
	return {"parent_field": parent_field, "ancestors": [a.name for a in ancestors], "children": children}


def _link_fields(doctype):
	"""(doctype, fieldname, parenttype-or-None) for every Link (or child-table link) to `doctype`."""
	key = f"folt:master_links:{doctype}"
	cached = frappe.cache.get_value(key)
	if cached is not None:
		return cached
	rows = frappe.get_all("DocField", filters={"fieldtype": "Link", "options": doctype}, fields=["parent", "fieldname"])
	rows += frappe.get_all("Custom Field", filters={"fieldtype": "Link", "options": doctype}, fields=["dt as parent", "fieldname"])
	out = []
	for r in rows:
		if r.fieldname == "old_parent" or not frappe.db.table_exists(r.parent):
			continue
		out.append((r.parent, r.fieldname, bool(frappe.get_meta(r.parent).istable)))
	frappe.cache.set_value(key, out, expires_in_sec=600)
	return out


def usage(doctype, name):
	"""Where a record is used: per linking doctype, how many records point at it, and the first few
	this person may open. Counts are counts; names are only shown where the person may read them."""
	out = []
	for linking, fieldname, istable in _link_fields(doctype):
		if istable:
			parents = frappe.db.sql(
				f"select parenttype, count(distinct parent) from `tab{linking}` where `{fieldname}` = %s group by parenttype", (name,)
			)
			for parenttype, n in parents:
				if n:
					sample = []
					if frappe.has_permission(parenttype, "read"):
						names = frappe.db.sql(f"select distinct parent from `tab{linking}` where `{fieldname}` = %s and parenttype = %s limit 5", (name, parenttype))
						sample = [p[0] for p in names if frappe.has_permission(parenttype, "read", p[0])]
					out.append({"doctype": parenttype, "field": fieldname, "count": n, "sample": sample})
			continue
		n = frappe.db.count(linking, {fieldname: name})
		if not n or (linking == doctype and fieldname.startswith("parent_")):
			continue
		sample = frappe.get_list(linking, filters={fieldname: name}, pluck="name", limit_page_length=5) if frappe.has_permission(linking, "read") else []
		out.append({"doctype": linking, "field": fieldname, "count": n, "sample": sample})
	out.sort(key=lambda u: -u["count"])
	return out


@frappe.whitelist()
def meta(doctype):
	"""The fields of a master doctype (and its child tables), for a form drawn from them."""
	_only_readers()
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
		"is_submittable": False,
		"is_tree": bool(m.is_tree),
		"title_field": m.title_field,
		"autoname": m.autoname,
		"fields": fields_of(m),
		"children": children,
		"writable_permlevels": m.get_permlevel_access("write") if doctype not in READ_ONLY else [],
	}


# --- writes -----------------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def save(doctype, doc, name=None):
	"""Save a master record (new or existing), or the Settings. Never moves its lifecycle.

	For an existing record only the fields sent are changed, and a child table sent is the whole
	table. `modified` travels with an existing record, so somebody else's newer save is refused
	rather than overwritten. Who may change a record at its current state is the workflow's
	`allow_edit` (workflow_access.enforce_state_custodian).
	"""
	_only_readers()
	_writable(doctype)
	values = parse_json(doc, {}) or {}
	for field in _PROTECTED:
		values.pop(field, None)
	m = frappe.get_meta(doctype)
	if m.issingle:
		target = frappe.get_doc(doctype)
		target.update(values)
		target.save()
	elif name:
		target = frappe.get_doc(doctype, name)
		target.update(values)
		target.save()
	else:
		values.pop("name", None)
		target = frappe.get_doc({"doctype": doctype, **values})
		target.insert()
	return {"name": target.name}


@frappe.whitelist(methods=["POST"])
def act(doctype, name, action, transition=None, reason=None):
	"""Move a master record along its lifecycle, or delete a draft that was never approved.

	A lifecycle move goes through apply_workflow exactly as the Desk's button does, with the reason
	for a turn-down parked first (workflow_access). A record that has ever been Active is never
	deleted: the controller refuses it, and Frappe refuses anything still linked.
	"""
	_only_readers()
	_writable(doctype)
	doc = frappe.get_doc(doctype, name)
	if action == "transition":
		if not transition:
			frappe.throw(_("Which step?"))
		if reason and reason.strip():
			hold_rejection_reason(doctype, name, reason.strip())
		apply_workflow(doc, transition)
		return {"name": doc.name}
	if action == "delete":
		frappe.delete_doc(doctype, name)
		return {"name": None}
	frappe.throw(_("Unknown action {0}.").format(action))


@frappe.whitelist(methods=["POST"])
def attach(doctype, docname):
	"""Frappe's own upload_file, private, onto a master record; Frappe checks the write right."""
	_only_readers()
	_writable(doctype)
	frappe.form_dict.update({"doctype": doctype, "docname": docname, "is_private": 1})
	from frappe.handler import upload_file

	f = upload_file()
	return {"name": f.name, "file_name": f.file_name, "file_url": f.file_url, "is_private": f.is_private}


# --- searches, trees and exports --------------------------------------------------------------


@frappe.whitelist()
def link_search(doctype, txt="", filters=None, page_length=20, include_inactive=0):
	"""Frappe's own link search over what a master form links to. Masters offer active records only."""
	_only_readers()
	if doctype not in LINK_TARGETS:
		frappe.throw(_("{0} is not searched from Programs master data.").format(doctype), frappe.PermissionError)
	flt = parse_json(filters) or {}
	if doctype in masterdata.MASTERS and not cint(include_inactive):
		if isinstance(flt, dict):
			flt = {**flt, "is_active": 1}
		else:
			flt = [*flt, [doctype, "is_active", "=", 1]]
	from frappe.desk.search import search_link

	return search_link(doctype, txt or "", filters=flt or None, page_length=min(cint(page_length) or 20, 50))


@frappe.whitelist()
def tree(doctype, parent=None):
	"""One level of a tree master: the roots, or the children of `parent`, with how many each has."""
	_only_readers()
	_doctype(doctype)
	meta = frappe.get_meta(doctype)
	if not meta.is_tree:
		frappe.throw(_("{0} is not a tree.").format(doctype))
	parent_field = meta.nsm_parent_field or f"parent_{frappe.scrub(doctype)}"
	title = _title_field(doctype)
	extra = [f for f in ("workflow_state", "location_level", MASTERS_CODE.get(doctype)) if f and meta.get_field(f)]
	rows = frappe.get_list(
		doctype,
		filters={parent_field: parent} if parent else {parent_field: ["is", "not set"]},
		fields=["name", title, "is_group", "lft", "rgt", *extra],
		order_by=f"{title} asc",
		limit_page_length=0,
	)
	for r in rows:
		r["descendants"] = max(0, (cint(r.pop("rgt")) - cint(r.pop("lft")) - 1) // 2)
		r["title"] = r.get(title)
	return {"parent": parent, "nodes": rows}


MASTERS_CODE = {dt: cfg[0] for dt, cfg in masterdata.MASTERS.items()}


@frappe.whitelist()
def export(doctype, q=None, filters=None, file_format="xlsx"):
	"""A master list as a spreadsheet: every row the person may see, with the list's filters."""
	_only_readers()
	_doctype(doctype)
	if not frappe.has_permission(doctype, "export"):
		frappe.throw(_("You may not export {0}.").format(_(doctype)), frappe.PermissionError)
	meta = frappe.get_meta(doctype)
	wanted, search, _date = DOCTYPES[doctype]
	cols = [{"fieldname": "name", "label": _("Name")}] + columns(doctype, wanted)
	if meta.get_field("workflow_state"):
		cols.append({"fieldname": "workflow_state", "label": _("Status")})
	where = _filters_for(doctype, filters)
	or_filters = [[doctype, f, "like", f"%{q}%"] for f in search if f == "name" or meta.get_field(f)] if q else None
	rows = frappe.get_list(doctype, fields=[c["fieldname"] for c in cols], filters=where, or_filters=or_filters, order_by="modified desc", limit_page_length=0)
	table = [[c["label"] for c in cols]] + [["" if r.get(c["fieldname"]) is None else r.get(c["fieldname"]) for c in cols] for r in rows]

	filename = f"{doctype} {nowdate()}"
	if file_format == "csv":
		buf = io.StringIO()
		csv.writer(buf).writerows(table)
		frappe.response.filename = f"{filename}.csv"
		frappe.response.filecontent = buf.getvalue()
	else:
		from frappe.utils.xlsxutils import make_xlsx

		frappe.response.filename = f"{filename}.xlsx"
		frappe.response.filecontent = make_xlsx(table, doctype[:31]).getvalue()
	frappe.response.type = "binary"


# --- approvals and audit ------------------------------------------------------------------------


def _lifecycle(doctype):
	"""Who may do what in a master's lifecycle, as its workflow says."""
	wf = get_workflow(doctype)
	states, moves = {}, {}
	for row in wf.states:
		states.setdefault(row.state, []).append(row.allow_edit)
	for t in wf.transitions:
		key = (t.state, t.action, t.next_state)
		m = moves.setdefault(key, {"from": t.state, "action": t.action, "to": t.next_state, "roles": [], "self_approval": bool(t.allow_self_approval)})
		m["roles"].append(t.allowed)
	return {
		"doctype": doctype,
		"workflow": wf.name,
		"states": [{"state": s, "roles": r} for s, r in states.items()],
		"transitions": list(moves.values()),
	}


@frappe.whitelist()
def approvals():
	"""What awaits this person's decision across the masters, what they have sent for review, and how
	each master's lifecycle is set (its states' custodians and its moves' roles)."""
	_only_readers()
	user = frappe.session.user
	waiting, mine, lifecycles = [], [], []
	for dt in masterdata.MASTERS:
		if not frappe.has_permission(dt, "read") or not get_workflow_name(dt):
			continue
		lifecycles.append(_lifecycle(dt))
		title = _title_field(dt)
		wf = get_workflow(dt)
		for r in frappe.get_list(dt, filters={"workflow_state": masterdata.PENDING}, fields=["name", title, "owner", "modified"],
		                         order_by="modified asc", limit_page_length=200):
			row = {"doctype": dt, "name": r.name, "title": r.get(title), "state": masterdata.PENDING, "owner": r.owner, "since": r.modified}
			if r.owner == user:
				mine.append(row)
			doc = frappe.get_doc(dt, r.name)
			moves = [
				{"action": t.action, "next_state": t.next_state, "needs_reason": is_turn_down(wf, masterdata.PENDING, t.next_state)}
				for t in get_transitions(doc)
				if has_approval_access(user, doc, t)
			]
			if moves:
				waiting.append({**row, "moves": moves})
		for r in frappe.get_list(dt, filters={"owner": user, "workflow_state": ["in", [masterdata.DRAFT, masterdata.REJECTED]]},
		                         fields=["name", title, "owner", "modified", "workflow_state"], order_by="modified desc", limit_page_length=50):
			mine.append({"doctype": dt, "name": r.name, "title": r.get(title), "state": r.workflow_state, "owner": r.owner, "since": r.modified})
	mine.sort(key=lambda r: str(r["since"]), reverse=True)
	return {"waiting": waiting, "mine": mine, "lifecycles": lifecycles}


@frappe.whitelist()
def audit(from_date=None, to_date=None, doctype=None, user=None, kind=None, start=0, page_length=100):
	"""Master data's audit trail: every saved change (Version) and every lifecycle move or comment,
	for the records this person may read. Nothing here is recorded by FoLT; it is the history the
	records already keep."""
	_only_readers()
	doctypes = [dt for dt in ([doctype] if doctype else list(masterdata.MASTERS)) if _doctype(dt) and frappe.has_permission(dt, "read")]
	if not doctypes:
		return {"events": [], "more": False}
	start, page_length = cint(start), min(cint(page_length) or 100, 500)
	window = []
	if from_date:
		window.append(["creation", ">=", f"{getdate(from_date)} 00:00:00"])
	if to_date:
		window.append(["creation", "<=", f"{getdate(to_date)} 23:59:59"])
	events = []
	if kind in (None, "", "change"):
		for v in frappe.get_all("Version", filters=[["ref_doctype", "in", doctypes], *window, *([["owner", "=", user]] if user else [])],
		                        fields=["ref_doctype", "docname", "owner", "creation", "data"], order_by="creation desc",
		                        limit_page_length=start + page_length):
			events.append({"kind": "change", "doctype": v.ref_doctype, "name": v.docname, "user": v.owner, "when": v.creation, "detail": _change(v.data)})
	if kind in (None, "", "workflow"):
		for c in frappe.get_all("Comment", filters=[["reference_doctype", "in", doctypes], ["comment_type", "in", ["Workflow", "Comment", "Info"]], *window,
		                                            *([["comment_email", "=", user]] if user else [])],
		                        fields=["reference_doctype", "reference_name", "comment_email", "owner", "creation", "content", "comment_type"],
		                        order_by="creation desc", limit_page_length=start + page_length):
			events.append({"kind": "workflow" if c.comment_type == "Workflow" else "comment", "doctype": c.reference_doctype, "name": c.reference_name,
			               "user": c.comment_email or c.owner, "when": c.creation, "detail": frappe.utils.strip_html(c.content or "")[:300]})
	readable = {}

	def can_read(dt, name):
		if (dt, name) not in readable:
			readable[(dt, name)] = bool(frappe.db.exists(dt, name)) and frappe.has_permission(dt, "read", name)
		return readable[(dt, name)]

	events = [e for e in events if can_read(e["doctype"], e["name"])]
	events.sort(key=lambda e: str(e["when"]), reverse=True)
	page = events[start : start + page_length]
	return {"events": page, "more": len(events) > start + page_length,
	        "link_titles": link_titles((e["doctype"], e["name"]) for e in page)}


# --- import: Frappe's own Data Import, one master at a time --------------------------------------

_NOT_IMPORTED = {"workflow_state", "is_active", "folt_rejection_reason", "lft", "rgt", "old_parent"}


def _importable(doctype):
	"""The fields a person fills in, in the doctype's order, by label (what Data Import reads)."""
	from frappe.model import no_value_fields

	meta = frappe.get_meta(doctype)
	return [df for df in meta.fields if df.fieldtype not in no_value_fields and df.fieldtype not in ("Table", "Table MultiSelect", "Attach", "Attach Image")
	        and not df.read_only and not df.hidden and df.fieldname not in _NOT_IMPORTED]


def _importable_doctype(doctype):
	_writable(doctype)
	if doctype not in masterdata.MASTERS:
		frappe.throw(_("{0} is not imported here.").format(_(doctype)), frappe.PermissionError)
	if not frappe.has_permission(doctype, "import"):
		frappe.throw(_("You may not import {0}.").format(_(doctype)), frappe.PermissionError)
	return doctype


@frappe.whitelist()
def import_template(doctype):
	"""A CSV with one column per field a person fills in, headed by its label; required ones marked."""
	_only_readers()
	_importable_doctype(doctype)
	buf = io.StringIO()
	csv.writer(buf).writerow([_(df.label) for df in _importable(doctype)])
	frappe.response.filename = f"{doctype} import template.csv"
	frappe.response.filecontent = buf.getvalue()
	frappe.response.type = "binary"


def _duplicates(doctype, preview):
	"""Rows whose name matches an existing record, or another row, once normalised (masterdata)."""
	_code, _prefix, title_field, dedupe = masterdata.MASTERS[doctype]
	label = _(frappe.get_meta(doctype).get_label(title_field))
	columns = preview.get("columns") or []
	idx = next((i for i, c in enumerate(columns) if (c.get("header_title") or "") == label or (c.get("df") or {}).get("fieldname") == title_field), None)
	if idx is None or not dedupe:
		return []
	existing = {masterdata.normalise(t): n for n, t in frappe.get_all(doctype, fields=["name", title_field], as_list=True)}
	seen, out = {}, []
	for row in preview.get("data") or []:
		row_no, values = row[0], row[1:]
		value = values[idx - 1] if idx and idx - 1 < len(values) else None
		key = masterdata.normalise(value)
		if not key:
			continue
		if key in existing:
			out.append(_("Row {0}: {1} looks like the existing {2}.").format(row_no, value, existing[key]))
		elif key in seen:
			out.append(_("Row {0}: {1} repeats row {2}.").format(row_no, value, seen[key]))
		else:
			seen[key] = row_no
	return out


def _preview(di):
	p = di.get_preview_from_template() or {}
	cols = [{"header": c.get("header_title"), "field": (c.get("df") or {}).get("label") if isinstance(c.get("df"), dict) else None,
	         "skipped": bool(c.get("skip_import"))} for c in p.get("columns") or []][1:]
	warnings = []
	for w in p.get("warnings") or []:
		where = f"{_('Row')} {w['row']}: " if w.get("row") else ""
		warnings.append(where + frappe.utils.strip_html(str(w.get("message") or "")))
	return {
		"name": di.name,
		"doctype": di.reference_doctype,
		"status": di.status,
		"columns": cols,
		"rows": [r[1:] for r in (p.get("data") or [])[:25]],
		"total": len(p.get("data") or []),
		"warnings": warnings,
		"duplicates": _duplicates(di.reference_doctype, p),
	}


@frappe.whitelist(methods=["POST"])
def import_upload(doctype):
	"""Start an import: a Data Import for `doctype` (insert, as the person), the uploaded file attached
	to it, and Frappe's preview of what it would import -- with its warnings and the rows that look
	like records already there. Nothing is imported yet."""
	_only_readers()
	_importable_doctype(doctype)
	di = frappe.get_doc({"doctype": "Data Import", "reference_doctype": doctype, "import_type": "Insert New Records"}).insert()
	frappe.form_dict.update({"doctype": "Data Import", "docname": di.name, "fieldname": "import_file", "is_private": 1})
	from frappe.handler import upload_file

	f = upload_file()
	di.reload()
	di.import_file = f.file_url
	di.save()
	return _preview(di)


@frappe.whitelist(methods=["POST"])
def import_run(name):
	"""Run a previewed import in the background. Each row is inserted as the person, through the
	master's own validation: it arrives in Draft, and a row that fails is logged, not half-saved."""
	_only_readers()
	di = frappe.get_doc("Data Import", name)
	_importable_doctype(di.reference_doctype)
	di.check_permission("write")
	return {"name": di.name, "started": bool(di.start_import())}


@frappe.whitelist()
def import_status(name):
	"""How far an import has got, and why each failed row failed."""
	_only_readers()
	from frappe.core.doctype.data_import.data_import import get_import_logs, get_import_status

	di = frappe.get_doc("Data Import", name)
	_doctype(di.reference_doctype)
	status = get_import_status(name)
	failed = []
	for log in get_import_logs(name, "failed"):
		messages = []
		for m in frappe.parse_json(log.messages or "[]") or []:
			messages.append(frappe.utils.strip_html(m.get("message") if isinstance(m, dict) else str(m)))
		failed.append({"rows": frappe.parse_json(log.row_indexes or "[]"), "messages": messages or [frappe.utils.strip_html((log.exception or "")[-300:])]})
	return {
		"name": name,
		"doctype": di.reference_doctype,
		"status": status.get("status"),
		"total": cint(status.get("total_records")),
		"imported": cint(status.get("success")),
		"failed": cint(status.get("failed")),
		"failures": failed,
	}


# --- reporting periods for a year ---------------------------------------------------------------

_SPANS = {"Monthly": 1, "Quarterly": 3, "Semiannual": 6, "Annual": 12}


@frappe.whitelist(methods=["POST"])
def generate_periods(fiscal_year, frequency, program=None, donor=None, deadline_days=30, responsible=None):
	"""Draft reporting periods covering a fiscal year at a frequency, each due `deadline_days` after
	it ends. A period already there (same name) is left alone. They are drafts: approving them makes
	them the reporting calendar."""
	_only_readers()
	from frappe.utils import add_days, add_months

	if frequency not in _SPANS:
		frappe.throw(_("Choose monthly, quarterly, semiannual or annual."))
	if not frappe.has_permission("FoLT Reporting Period", "create"):
		frappe.throw(_("You may not create reporting periods."), frappe.PermissionError)
	start, end = frappe.db.get_value("Fiscal Year", fiscal_year, ["year_start_date", "year_end_date"]) or (None, None)
	if not start:
		frappe.throw(_("No fiscal year {0}.").format(fiscal_year))
	span, n, created, kept = _SPANS[frequency], 0, [], []
	whose = program or donor or ""
	cur = getdate(start)
	while cur <= getdate(end):
		n += 1
		last = min(add_days(add_months(cur, span), -1), getdate(end))
		label = {"Monthly": cur.strftime("%b %Y"), "Quarterly": f"Q{n}", "Semiannual": f"H{n}", "Annual": ""}[frequency]
		title = " ".join(x for x in (fiscal_year, label, f"— {whose}" if whose else "") if x)
		if frappe.db.exists("FoLT Reporting Period", {"period_name": title}):
			kept.append(title)
		else:
			doc = frappe.get_doc({"doctype": "FoLT Reporting Period", "period_name": title, "frequency": frequency, "program": program, "donor": donor,
			                      "start_date": cur, "end_date": last, "deadline": add_days(last, cint(deadline_days)), "responsible": responsible})
			doc.insert()
			created.append(doc.name)
		cur = add_days(last, 1)
	return {"created": created, "kept": kept}
