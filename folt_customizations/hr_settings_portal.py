"""HR settings without the Desk (ADR-009): the allow-listed edge the FoLT app's HR > Settings area
reads and writes through, run as the user.

The same shape as procurement_portal and finance_portal, on portal_common's document half: this
module decides WHAT is part of HR settings and WHO may come in; Frappe's permissions and the
doctypes' own controllers decide everything else.

Who (the user's choice, 2026-11-03): HR Manager (and System Manager) change HR settings, HR User
reads them. That rule is Frappe's own too -- permissions.HR_READ_ONLY takes HR User's writes away
on every migrate -- and it is asked again here, so a write from HR User is refused at the door even
on a site where an administrator gave a permission back.

DOCTYPES grows by phase: phase 1 is the general settings and the organisation lists HR keeps;
holidays, leave, pay set-up and onboarding come after.
"""

import frappe
from frappe import _

from folt_customizations import portal_common as _common

READERS = ("HR User", "HR Manager", "System Manager")
EDITORS = ("HR Manager", "System Manager")

# doctype -> (group, list columns, searched fields). Columns a doctype's meta does not have on this
# version are dropped at run time (portal_common.columns).
DOCTYPES = {
	# general (phase 1)
	"HR Settings": ("general", [], []),
	"Payroll Settings": ("general", [], []),
	"Department": ("organisation", ["department_name", "parent_department", "company", "is_group", "disabled"], ["name", "department_name"]),
	"Designation": ("organisation", ["designation_name"], ["name", "designation_name"]),
	"Employment Type": ("organisation", ["employee_type_name"], ["name", "employee_type_name"]),
	"Branch": ("organisation", ["branch"], ["name", "branch"]),
	"Employee Grade": ("organisation", ["default_salary_structure", "currency", "default_base_pay"], ["name"]),
}

# The setters a controller's own validate() runs, for a preview of an unsaved form. Nothing in
# phase 1 computes anything, so every doctype gets ERPNext's set_missing_values if it has one.
_PREVIEW = {}


def _roles():
	return set(frappe.get_roles())


def _reader():
	if frappe.session.user != "Administrator" and not _roles() & set(READERS):
		frappe.throw(_("HR settings are open to HR Users, HR Managers and System Managers."), frappe.PermissionError)


def _editor() -> bool:
	return frappe.session.user == "Administrator" or bool(_roles() & set(EDITORS))


def _doctype(doctype: str) -> str:
	if doctype not in DOCTYPES:
		frappe.throw(_("{0} is not part of HR settings.").format(doctype), frappe.PermissionError)
	_reader()
	return doctype


def _writable(doctype: str) -> str:
	_doctype(doctype)
	if not _editor():
		frappe.throw(_("HR settings are changed by HR Managers. You can read them."), frappe.PermissionError)
	return doctype


def _may(doctype, ptype="read"):
	try:
		return bool(frappe.has_permission(doctype, ptype))
	except Exception:  # noqa: BLE001 -- a doctype missing on this version is simply not offered
		return False


def _count(doctype):
	if frappe.get_meta(doctype).issingle or not _may(doctype):
		return None
	return frappe.db.count(doctype) if frappe.has_permission(doctype, "read") else None


# --- context ----------------------------------------------------------------------------------


@frappe.whitelist()
def context():
	"""What every HR settings screen needs to know first: what the reader may do with each doctype,
	and how many of each there are (the hub's figures)."""
	_reader()
	editor = _editor()
	capabilities, counts = {}, {}
	for doctype in DOCTYPES:
		if not frappe.db.exists("DocType", doctype):
			continue
		caps = {ptype: _may(doctype, ptype) for ptype in ("read", "create", "write", "submit", "cancel", "delete")}
		if not editor:
			caps.update({"create": False, "write": False, "submit": False, "cancel": False, "delete": False})
		capabilities[doctype] = caps
		counts[doctype] = _count(doctype)
	companies = frappe.get_all("Company", fields=["name", "abbr", "default_currency"], order_by="name")
	default_company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value("Global Defaults", "default_company")
	return {
		"user": frappe.session.user,
		"companies": companies,
		"default_company": default_company if default_company in {c.name for c in companies} else (companies[0].name if companies else None),
		"capabilities": capabilities,
		"counts": counts,
		"workflows": {},
		"roles": {"editor": editor},
	}


# --- lists and records ------------------------------------------------------------------------


@frappe.whitelist()
def records(doctype, q=None, filters=None, order_by=None, start=0, page_length=20):
	"""One page of an HR settings list, as the reader may see it, with its total."""
	_doctype(doctype)
	_group, wanted, search = DOCTYPES[doctype]
	return _common.list_view(doctype, wanted, search, q, filters, order_by, start, page_length)


@frappe.whitelist()
def record(doctype, name=None):
	"""A settings record with its history and what the reader may do with it."""
	_doctype(doctype)
	out = _common.record_view(doctype, name)
	if not _editor():
		out["permissions"].update({"write": False, "create": False, "submit": False, "cancel": False, "amend": False, "delete": False})
	return out


@frappe.whitelist()
def meta(doctype):
	"""The fields of an HR settings doctype (and its child tables), for a form drawn from them."""
	_doctype(doctype)
	return _common.meta_view(doctype)


# --- writes -----------------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def save(doctype, doc, name=None):
	"""Save a record, or a single settings record. Never submits (portal_common.save_doc)."""
	_writable(doctype)
	target = _common.save_doc(doctype, doc, name)
	return {"name": target.name}


@frappe.whitelist(methods=["POST"])
def preview(doctype, doc):
	"""ERPNext's own defaults for an unsaved form. Writes nothing."""
	_writable(doctype)
	return _common.preview_doc(doctype, doc, _PREVIEW.get(doctype, ("set_missing_values",)))


@frappe.whitelist(methods=["POST"])
def act(doctype, name, action, transition=None, reason=None):
	"""Submit, cancel, amend or delete a record."""
	_writable(doctype)
	return _common.act_on(doctype, name, action, transition, reason)


@frappe.whitelist(methods=["POST"])
def attach(doctype, docname):
	"""Frappe's own upload_file, private, onto a settings record; Frappe checks the write right."""
	_writable(doctype)
	return _common.attach_file(doctype, docname)


@frappe.whitelist()
def link_search(doctype, txt="", filters=None, page_length=20):
	"""Frappe's own link search, for the settings forms' link fields."""
	_reader()
	return _common.link_search(doctype, txt, filters, page_length)
