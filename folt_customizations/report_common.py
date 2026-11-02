"""What FoLT's procurement Script Reports share (folt_customizations/report/*, ADR-008)."""

import frappe
from frappe.utils import getdate


def period(doctype, date_field, filters):
	"""The company and date filters every procurement report takes, as get_list filters."""
	out = []
	if filters.get("company"):
		out.append([doctype, "company", "=", filters.company])
	if filters.get("from_date"):
		out.append([doctype, date_field, ">=", str(getdate(filters.from_date))])
	if filters.get("to_date"):
		out.append([doctype, date_field, "<=", str(getdate(filters.to_date))])
	return out


def readable(doctype, **kwargs):
	"""Related documents a report reads beside its own rows, as the reader may see them: Frappe's
	get_list (the reader's permissions and user permissions), or nothing when the reader may not
	read that doctype at all -- a column left empty rather than a report refused (ADR-008 phase 8)."""
	if not frappe.has_permission(doctype, "read"):
		return []
	return frappe.get_list(doctype, limit_page_length=0, **kwargs)
