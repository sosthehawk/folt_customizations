"""What FoLT's procurement Script Reports share (folt_customizations/report/*, ADR-008)."""

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
