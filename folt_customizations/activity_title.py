"""The activity title: the words an activity is known by, from requisition to retirement.

An activity runs through five Programs documents -- the requisition, the float request, the
attendance register, the reimbursement list and the float retirement -- and each had its own code
(REQ-2026-00002, HR-EAD-2026-00001, APL-..., PRL-..., HR-EXP-...). Only the requisition said what the
activity was; a float was headed by the employee's name and a reimbursement list by its code, so
following one activity meant opening every document. Now every step carries:

* the activity title -- on the requisition it is what the requester types ("Activity title",
  `activity_program`); the attendance register's `activity_title`; `folt_activity_title` on the
  float, the reimbursement list and the retirement;
* the requisition reference -- `folt_activity_requisition` (the register's `activity_requisition`),
  read-only, set from the document each one was made from.

`carry` fills both on every save from the first upstream document that has them; a typed title is
never overwritten, except the register's, which is read-only and always follows its requisition.
Codes, names and links are untouched: this is how documents are named and found, nothing else.
"""

import frappe

AR = "Activity Requisition"
EA = "Employee Advance"
APL = "Activity Participant List"
PRL = "Participant Reimbursement List"
EC = "Expense Claim"

# doctype -> (its title field, its requisition reference field)
FIELDS = {
	AR: ("activity_program", "name"),
	EA: ("folt_activity_title", "folt_activity_requisition"),
	APL: ("activity_title", "activity_requisition"),
	PRL: ("folt_activity_title", "folt_activity_requisition"),
	EC: ("folt_activity_title", "folt_activity_requisition"),
}
CHAIN = tuple(FIELDS)

# doctype -> where it names an upstream document, most trusted first:
# (child table field or None for the header, link field, the doctype it names).
UPSTREAM = {
	EA: [(None, "folt_activity_requisition", AR)],
	APL: [(None, "activity_requisition", AR)],
	PRL: [(None, "employee_advance", EA), (None, "attendance_reference", APL)],
	EC: [(None, "folt_reimbursement_list", PRL), ("advances", "employee_advance", EA)],
}
# A document that came from no requisition is still named by its activity (a Project).
PROJECT_FIELD = {EA: "folt_project", PRL: "activity", EC: "project"}


def title_field(doctype: str) -> str | None:
	return FIELDS[doctype][0] if doctype in FIELDS else None


def ref_field(doctype: str) -> str | None:
	"""Where a chain document names its requisition ("name" on the requisition itself)."""
	return FIELDS[doctype][1] if doctype in FIELDS else None


def _linked(doc, table, field):
	if table is None:
		value = doc.get(field)
		return [value] if value else []
	return list(dict.fromkeys(r.get(field) for r in doc.get(table) or [] if r.get(field)))


def _source(doctype, name):
	"""(requisition, title) of one upstream document, read without a permission check: the reader
	already holds the link, and only these two values travel."""
	title, ref = FIELDS[doctype]
	if ref == "name":
		value = frappe.db.get_value(doctype, name, title)
		return (name, value) if value is not None else (None, None)
	row = frappe.db.get_value(doctype, name, [ref, title], as_dict=True)
	return (row.get(ref), row.get(title)) if row else (None, None)


def resolve(doc):
	"""The requisition and activity title this document inherits, or (None, None)."""
	ref = title = None
	for table, field, target in UPSTREAM.get(doc.doctype, ()):
		for name in _linked(doc, table, field):
			r, t = _source(target, name)
			ref, title = ref or r, title or t
			if ref and title:
				return ref, title
	if not title and doc.doctype in PROJECT_FIELD and doc.get(PROJECT_FIELD[doc.doctype]):
		title = frappe.db.get_value("Project", doc.get(PROJECT_FIELD[doc.doctype]), "project_name")
	return ref, title


def _applies(doc) -> bool:
	if doc.doctype not in UPSTREAM:
		return False
	title, ref = FIELDS[doc.doctype]
	if not (doc.meta.has_field(title) and doc.meta.has_field(ref)):
		return False
	# A float or a claim with no activity behind it (a staff advance, a travel claim) is not on the
	# chain and keeps no activity fields.
	if doc.doctype in (EA, EC):
		return any(_linked(doc, t, f) for t, f, _ in UPSTREAM[doc.doctype]) or bool(doc.get(PROJECT_FIELD[doc.doctype]))
	return True


def carry(doc, method=None):
	"""validate on every activity document after the requisition: fill what is empty."""
	if not _applies(doc):
		return
	title_f, ref_f = FIELDS[doc.doctype]
	# The register's title is read-only and fetched from its Project; it follows the requisition.
	follows = doc.doctype == APL
	if doc.get(ref_f) and doc.get(title_f) and not follows:
		return
	ref, title = resolve(doc)
	if ref and not doc.get(ref_f):
		doc.set(ref_f, ref)
	if title and (follows or not doc.get(title_f)):
		doc.set(title_f, title)


def backfill():
	"""Give the documents saved before the fields existed their requisition and title, upstream
	first so each step inherits from the one before. Written straight to the row (no save, no
	`modified` change): the documents themselves, submitted or not, are not touched otherwise."""
	for doctype in (EA, APL, PRL, EC):
		title_f, ref_f = FIELDS[doctype]
		meta = frappe.get_meta(doctype)
		if not (meta.has_field(title_f) and meta.has_field(ref_f)):
			continue
		for name in frappe.get_all(doctype, or_filters={title_f: ["in", ["", None]], ref_f: ["in", ["", None]]}, pluck="name"):
			doc = frappe.get_doc(doctype, name)
			if not _applies(doc):
				continue
			ref, title = resolve(doc)
			values = {}
			if ref and not doc.get(ref_f):
				values[ref_f] = ref
			if title and not doc.get(title_f):
				values[title_f] = title
			if values:
				frappe.db.set_value(doctype, name, values, update_modified=False)
