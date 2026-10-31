"""What the app's allow-listed edges share (finance_portal, programs_portal).

Each portal keeps its own allow-list and role floor; these are the pieces that do not depend on
either: reading a JSON argument, a list's columns from the meta, filters checked against the meta,
and a one-line summary of a Version.
"""

import frappe
from frappe import _

FILTER_OPS = {"=", "!=", ">", "<", ">=", "<=", "like", "not like", "in", "not in", "between", "is"}
STD_FIELDS = {"name", "owner", "creation", "modified", "modified_by", "docstatus", "amended_from"}


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
