"""Every request for quotation in the period: its deadline, how many suppliers were invited and
how many bid, the lowest submitted bid, and the award route taken (committee evaluation or waiver)."""

import frappe
from frappe import _
from frappe.utils import flt, get_datetime, now_datetime

from folt_customizations.report_common import period


def execute(filters=None):
	f = frappe._dict(filters or {})
	rfqs = frappe.get_list("Request for Quotation", filters=[["docstatus", "<", 2], *period("Request for Quotation", "transaction_date", f)],
	                       fields=["name", "transaction_date", "schedule_date", "folt_quotation_deadline", "status", "docstatus"],
	                       order_by="transaction_date desc, name desc", limit_page_length=0)
	names = [r.name for r in rfqs] or [""]
	invited = {}
	for s in frappe.get_all("Request for Quotation Supplier", filters={"parent": ["in", names]}, fields=["parent"]):
		invited[s.parent] = invited.get(s.parent, 0) + 1
	bids = {}
	for i in frappe.get_all("Supplier Quotation Item", filters={"request_for_quotation": ["in", names], "docstatus": 1}, fields=["request_for_quotation", "parent"], distinct=True):
		bids.setdefault(i.request_for_quotation, set()).add(i.parent)
	heads = {q.name: q for q in frappe.get_all("Supplier Quotation", filters={"name": ["in", [p for s in bids.values() for p in s] or [""]]},
	                                           fields=["name", "supplier_name", "base_grand_total"])}
	evals = {e.request_for_quotation: e for e in frappe.get_all("Procurement Committee Evaluation", filters={"request_for_quotation": ["in", names], "docstatus": ["<", 2]},
	                                                             fields=["name", "request_for_quotation", "workflow_state"])}
	rows = []
	for r in rfqs:
		qs = [heads[q] for q in bids.get(r.name, ()) if q in heads]
		low = min(qs, key=lambda q: flt(q.base_grand_total)) if qs else None
		ev = evals.get(r.name)
		rows.append({**r, "invited": invited.get(r.name, 0), "bids": len(qs), "lowest_supplier": low.supplier_name if low else None,
		             "lowest_total": flt(low.base_grand_total) if low else None, "evaluation": ev.name if ev else None,
		             "evaluation_state": ev.workflow_state if ev else None,
		             "open": bool(r.docstatus == 1 and r.folt_quotation_deadline and now_datetime() <= get_datetime(r.folt_quotation_deadline))})
	columns = [
		{"fieldname": "name", "label": _("RFQ"), "fieldtype": "Link", "options": "Request for Quotation", "width": 160},
		{"fieldname": "transaction_date", "label": _("Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "folt_quotation_deadline", "label": _("Deadline"), "fieldtype": "Datetime", "width": 150},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 100},
		{"fieldname": "invited", "label": _("Invited"), "fieldtype": "Int", "width": 80},
		{"fieldname": "bids", "label": _("Bids"), "fieldtype": "Int", "width": 70},
		{"fieldname": "lowest_supplier", "label": _("Lowest bid from"), "fieldtype": "Data", "width": 180},
		{"fieldname": "lowest_total", "label": _("Lowest total"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "evaluation", "label": _("Committee evaluation"), "fieldtype": "Link", "options": "Procurement Committee Evaluation", "width": 150},
		{"fieldname": "evaluation_state", "label": _("Evaluation state"), "fieldtype": "Data", "width": 170},
	]
	return columns, rows
