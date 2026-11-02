"""Procurement spend in the period by department, project, cost centre, supplier or item group:
submitted orders' (or supplier invoices') own line amounts, base_net_amount, added up."""

import frappe
from frappe import _
from frappe.utils import flt

from folt_customizations.report_common import period

_PARENT = {"Ordered": ("Purchase Order", "Purchase Order Item", "transaction_date"), "Invoiced": ("Purchase Invoice", "Purchase Invoice Item", "posting_date")}
_FIELD = {"Department": "folt_department", "Project": "project", "Cost Center": "cost_center", "Supplier": "supplier", "Item Group": "item_group"}


def execute(filters=None):
	f = frappe._dict(filters or {})
	parent, child, date = _PARENT.get(f.basis or "Ordered", _PARENT["Ordered"])
	by = f.dimension or "Department"
	field = _FIELD.get(by, "folt_department")
	heads = {h.name: h for h in frappe.get_list(parent, filters=[["docstatus", "=", 1], *period(parent, date, f)],
	                                            fields=["name", "supplier", "supplier_name", "folt_department"], limit_page_length=0)}
	totals = {}
	for line in frappe.get_all(child, filters={"parent": ["in", list(heads) or [""]]}, fields=["parent", "project", "cost_center", "item_group", "base_net_amount"]):
		h = heads[line.parent]
		key = line.get(field) if field in ("project", "cost_center", "item_group") else h.get(field)
		label = h.supplier_name if field == "supplier" and key else key
		row = totals.setdefault(key or "", {"key": key, "label": label or _("Not set"), "amount": 0.0, "documents": set()})
		row["amount"] += flt(line.base_net_amount)
		row["documents"].add(line.parent)
	grand = sum(r["amount"] for r in totals.values()) or 1
	rows = sorted(({"key": r["key"], "label": r["label"], "amount": flt(r["amount"], 2), "documents": len(r["documents"]),
	                "share": flt(r["amount"] / grand * 100, 1)} for r in totals.values()), key=lambda r: -r["amount"])
	columns = [
		{"fieldname": "label", "label": _(by), "fieldtype": "Data", "width": 240},
		{"fieldname": "documents", "label": _("Orders") if parent == "Purchase Order" else _("Invoices"), "fieldtype": "Int", "width": 90},
		{"fieldname": "amount", "label": _("Spend"), "fieldtype": "Currency", "width": 140},
		{"fieldname": "share", "label": _("Share"), "fieldtype": "Percent", "width": 90},
	]
	chart = {"data": {"labels": [r["label"] for r in rows[:10]], "datasets": [{"name": _("Spend"), "values": [r["amount"] for r in rows[:10]]}]}, "type": "bar"}
	return columns, rows, None, chart
