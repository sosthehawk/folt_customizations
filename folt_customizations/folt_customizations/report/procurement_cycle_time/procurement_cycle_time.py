"""How long buying takes, order by order: from the requisition to the RFQ, to the first bid, to the
order, to the first receipt and to the first invoice -- each date read off the documents themselves."""

import frappe
from frappe import _
from frappe.utils import date_diff

from folt_customizations.report_common import period


def _first(child, link, values, parent_doctype, date_field):
	rows = frappe.get_all(child, filters={link: ["in", values or [""]], "docstatus": 1}, fields=[link, "parent"])
	dates = dict(frappe.get_all(parent_doctype, filters={"name": ["in", list({r.parent for r in rows}) or [""]]}, fields=["name", date_field], as_list=True))
	out = {}
	for r in rows:
		d = dates.get(r.parent)
		if d and (r.get(link) not in out or d < out[r.get(link)]):
			out[r.get(link)] = d
	return out


def execute(filters=None):
	f = frappe._dict(filters or {})
	orders = frappe.get_list("Purchase Order", filters=[["docstatus", "=", 1], *period("Purchase Order", "transaction_date", f)],
	                         fields=["name", "supplier_name", "transaction_date"], order_by="transaction_date desc", limit_page_length=0)
	names = [o.name for o in orders]
	lines = frappe.get_all("Purchase Order Item", filters={"parent": ["in", names or [""]]}, fields=["parent", "material_request", "supplier_quotation"])
	mr_of, sq_of = {}, {}
	for l in lines:
		if l.material_request:
			mr_of.setdefault(l.parent, l.material_request)
		if l.supplier_quotation:
			sq_of.setdefault(l.parent, l.supplier_quotation)
	mr_dates = dict(frappe.get_all("Material Request", filters={"name": ["in", list(mr_of.values()) or [""]]}, fields=["name", "transaction_date"], as_list=True))
	sq_dates = dict(frappe.get_all("Supplier Quotation", filters={"name": ["in", list(sq_of.values()) or [""]]}, fields=["name", "transaction_date"], as_list=True))
	rfq_of = dict(frappe.get_all("Supplier Quotation Item", filters={"parent": ["in", list(sq_of.values()) or [""]], "request_for_quotation": ["is", "set"]},
	                             fields=["parent", "request_for_quotation"], as_list=True))
	rfq_dates = dict(frappe.get_all("Request for Quotation", filters={"name": ["in", list(rfq_of.values()) or [""]]}, fields=["name", "transaction_date"], as_list=True))
	receipt = _first("Purchase Receipt Item", "purchase_order", names, "Purchase Receipt", "posting_date")
	invoice = _first("Purchase Invoice Item", "purchase_order", names, "Purchase Invoice", "posting_date")
	gap = lambda a, b: date_diff(b, a) if a and b else None  # noqa: E731
	rows = []
	for o in orders:
		mr = mr_dates.get(mr_of.get(o.name))
		sq = sq_dates.get(sq_of.get(o.name))
		rfq = rfq_dates.get(rfq_of.get(sq_of.get(o.name)))
		rows.append({"name": o.name, "supplier": o.supplier_name, "requisition": mr, "rfq": rfq, "bid": sq, "ordered": o.transaction_date,
		             "received": receipt.get(o.name), "invoiced": invoice.get(o.name),
		             "req_to_order": gap(mr, o.transaction_date), "order_to_receipt": gap(o.transaction_date, receipt.get(o.name)),
		             "receipt_to_invoice": gap(receipt.get(o.name), invoice.get(o.name)), "total": gap(mr or rfq or o.transaction_date, invoice.get(o.name) or receipt.get(o.name))})
	columns = [
		{"fieldname": "name", "label": _("Order"), "fieldtype": "Link", "options": "Purchase Order", "width": 150},
		{"fieldname": "supplier", "label": _("Supplier"), "fieldtype": "Data", "width": 170},
		{"fieldname": "requisition", "label": _("Requested"), "fieldtype": "Date", "width": 100},
		{"fieldname": "rfq", "label": _("RFQ sent"), "fieldtype": "Date", "width": 100},
		{"fieldname": "bid", "label": _("Bid"), "fieldtype": "Date", "width": 100},
		{"fieldname": "ordered", "label": _("Ordered"), "fieldtype": "Date", "width": 100},
		{"fieldname": "received", "label": _("First receipt"), "fieldtype": "Date", "width": 100},
		{"fieldname": "invoiced", "label": _("First invoice"), "fieldtype": "Date", "width": 100},
		{"fieldname": "req_to_order", "label": _("Request to order (days)"), "fieldtype": "Int", "width": 120},
		{"fieldname": "order_to_receipt", "label": _("Order to receipt (days)"), "fieldtype": "Int", "width": 120},
		{"fieldname": "receipt_to_invoice", "label": _("Receipt to invoice (days)"), "fieldtype": "Int", "width": 120},
		{"fieldname": "total", "label": _("Start to finish (days)"), "fieldtype": "Int", "width": 120},
	]
	avg = lambda k: round(sum(r[k] for r in rows if r[k] is not None) / max(1, sum(1 for r in rows if r[k] is not None)), 1)  # noqa: E731
	summary = [{"label": _("Request to order"), "value": avg("req_to_order"), "datatype": "Float"},
	           {"label": _("Order to receipt"), "value": avg("order_to_receipt"), "datatype": "Float"},
	           {"label": _("Receipt to invoice"), "value": avg("receipt_to_invoice"), "datatype": "Float"}]
	return columns, rows, None, None, summary
