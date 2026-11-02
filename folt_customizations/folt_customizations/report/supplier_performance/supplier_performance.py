"""Each supplier's record in the period: orders and their value, how fully received orders arrived
against their required-by date, what was rejected on receipt, invoices that did not match, and its
Supplier Scorecard -- all read from the documents."""

import frappe
from frappe import _
from frappe.utils import date_diff, flt

from folt_customizations.report_common import period


def execute(filters=None):
	f = frappe._dict(filters or {})
	where = [["docstatus", "=", 1], *period("Purchase Order", "transaction_date", f)]
	if f.supplier:
		where.append(["supplier", "=", f.supplier])
	orders = frappe.get_list("Purchase Order", filters=where, fields=["name", "supplier", "supplier_name", "base_grand_total", "schedule_date", "per_received"], limit_page_length=0)
	names = [o.name for o in orders] or [""]
	last = {}
	recv = frappe.get_all("Purchase Receipt Item", filters={"purchase_order": ["in", names], "docstatus": 1}, fields=["purchase_order", "parent", "received_qty", "rejected_qty"])
	pr_dates = dict(frappe.get_all("Purchase Receipt", filters={"name": ["in", list({r.parent for r in recv}) or [""]]}, fields=["name", "posting_date"], as_list=True))
	got, rejected = {}, {}
	for r in recv:
		d = pr_dates.get(r.parent)
		if d and (r.purchase_order not in last or d > last[r.purchase_order]):
			last[r.purchase_order] = d
		got[r.purchase_order] = got.get(r.purchase_order, 0) + flt(r.received_qty)
		rejected[r.purchase_order] = rejected.get(r.purchase_order, 0) + flt(r.rejected_qty)
	unmatched = {}
	for p in frappe.get_all("Purchase Invoice", filters={"docstatus": 1, "folt_match_status": "Discrepancies", "supplier": ["in", list({o.supplier for o in orders}) or [""]]}, fields=["supplier"]):
		unmatched[p.supplier] = unmatched.get(p.supplier, 0) + 1
	scores = {s.name: s for s in frappe.get_all("Supplier Scorecard", fields=["name", "supplier_score", "status"])}
	by = {}
	for o in orders:
		s = by.setdefault(o.supplier, {"supplier": o.supplier, "supplier_name": o.supplier_name, "orders": 0, "value": 0.0, "received_orders": 0, "on_time": 0,
		                               "late_days": [], "received_qty": 0.0, "rejected_qty": 0.0})
		s["orders"] += 1
		s["value"] += flt(o.base_grand_total)
		s["received_qty"] += got.get(o.name, 0)
		s["rejected_qty"] += rejected.get(o.name, 0)
		if flt(o.per_received) >= 100 and last.get(o.name) and o.schedule_date:
			s["received_orders"] += 1
			late = date_diff(last[o.name], o.schedule_date)
			if late > 0:
				s["late_days"].append(late)
			else:
				s["on_time"] += 1
	rows = []
	for s in by.values():
		sc = scores.get(s["supplier"])
		rows.append({**{k: v for k, v in s.items() if k != "late_days"}, "value": flt(s["value"], 2),
		             "on_time_pct": flt(s["on_time"] / s["received_orders"] * 100, 1) if s["received_orders"] else None,
		             "average_days_late": round(sum(s["late_days"]) / len(s["late_days"]), 1) if s["late_days"] else None,
		             "rejected_pct": flt(s["rejected_qty"] / s["received_qty"] * 100, 1) if s["received_qty"] else None,
		             "unmatched_invoices": unmatched.get(s["supplier"], 0), "score": flt(sc.supplier_score) if sc else None, "standing": sc.status if sc else None})
	rows.sort(key=lambda r: -r["value"])
	columns = [
		{"fieldname": "supplier", "label": _("Supplier"), "fieldtype": "Link", "options": "Supplier", "width": 180},
		{"fieldname": "orders", "label": _("Orders"), "fieldtype": "Int", "width": 70},
		{"fieldname": "value", "label": _("Ordered"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "received_orders", "label": _("Fully received"), "fieldtype": "Int", "width": 100},
		{"fieldname": "on_time_pct", "label": _("On time %"), "fieldtype": "Percent", "width": 90},
		{"fieldname": "average_days_late", "label": _("Avg days late"), "fieldtype": "Float", "width": 100},
		{"fieldname": "rejected_pct", "label": _("Rejected %"), "fieldtype": "Percent", "width": 90},
		{"fieldname": "unmatched_invoices", "label": _("Unmatched invoices"), "fieldtype": "Int", "width": 120},
		{"fieldname": "score", "label": _("Scorecard"), "fieldtype": "Float", "width": 90},
		{"fieldname": "standing", "label": _("Standing"), "fieldtype": "Data", "width": 100},
	]
	return columns, rows
