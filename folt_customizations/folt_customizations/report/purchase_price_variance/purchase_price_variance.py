"""Supplier invoice lines against what their orders said, and against the item's last purchase rate:
the billed unit price, the ordered one, the difference and its share (base currency)."""

import frappe
from frappe import _
from frappe.utils import flt

from folt_customizations.report_common import period, readable


def execute(filters=None):
	f = frappe._dict(filters or {})
	where = [["docstatus", "=", 1], ["is_return", "=", 0], *period("Purchase Invoice", "posting_date", f)]
	if f.supplier:
		where.append(["supplier", "=", f.supplier])
	heads = {h.name: h for h in frappe.get_list("Purchase Invoice", filters=where, fields=["name", "posting_date", "supplier", "supplier_name"], limit_page_length=0)}
	lines = frappe.get_all("Purchase Invoice Item", filters={"parent": ["in", list(heads) or [""]], "po_detail": ["is", "set"]},
	                       fields=["parent", "item_code", "item_name", "qty", "base_rate", "purchase_order", "po_detail"])
	po_rates = dict(frappe.get_all("Purchase Order Item", filters={"name": ["in", [l.po_detail for l in lines] or [""]]}, fields=["name", "base_rate"], as_list=True))
	last = dict(readable("Item", filters={"name": ["in", list({l.item_code for l in lines}) or [""]]}, fields=["name", "last_purchase_rate"], as_list=True))
	rows = []
	for l in lines:
		h = heads[l.parent]
		ordered = flt(po_rates.get(l.po_detail))
		diff = flt(l.base_rate) - ordered
		rows.append({"invoice": l.parent, "posting_date": h.posting_date, "supplier": h.supplier_name or h.supplier, "item_code": l.item_code,
		             "item_name": l.item_name, "qty": flt(l.qty), "purchase_order": l.purchase_order, "ordered_rate": ordered, "billed_rate": flt(l.base_rate),
		             "variance": flt(diff, 2), "variance_pct": flt(diff / ordered * 100, 2) if ordered else None,
		             "variance_amount": flt(diff * flt(l.qty), 2), "last_purchase_rate": flt(last.get(l.item_code))})
	columns = [
		{"fieldname": "invoice", "label": _("Invoice"), "fieldtype": "Link", "options": "Purchase Invoice", "width": 160},
		{"fieldname": "posting_date", "label": _("Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "supplier", "label": _("Supplier"), "fieldtype": "Data", "width": 180},
		{"fieldname": "item_name", "label": _("Item"), "fieldtype": "Data", "width": 180},
		{"fieldname": "purchase_order", "label": _("Order"), "fieldtype": "Link", "options": "Purchase Order", "width": 150},
		{"fieldname": "qty", "label": _("Qty"), "fieldtype": "Float", "width": 70},
		{"fieldname": "ordered_rate", "label": _("Ordered at"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "billed_rate", "label": _("Billed at"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "variance", "label": _("Difference"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "variance_pct", "label": _("Difference %"), "fieldtype": "Percent", "width": 100},
		{"fieldname": "variance_amount", "label": _("Difference x qty"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "last_purchase_rate", "label": _("Last purchase rate"), "fieldtype": "Currency", "width": 120},
	]
	return columns, rows
