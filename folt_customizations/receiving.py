"""Goods and services received against an order (ADR-008, phase 5): ERPNext's Purchase Receipt.

ERPNext keeps the quantities -- received, accepted, rejected per line, what the order still awaits,
and the over-receipt limit (Stock Settings' over_delivery_receipt_allowance). Two things are added:

*A rejection says why.* A receipt that rejects anything, or whose inspection failed, carries the
receiving officer's discrepancy notes before it is saved -- the one thing the buyer chasing the
supplier needs.

*A service line is held to its order too.* ERPNext refuses receiving more than an order allows
only for stock items (status_updater.fetch_items_with_pending_qty filters on is_stock_item), and
FoLT mostly buys services -- car hire, live-streaming, catering. before_submit applies ERPNext's
own rule to the lines it skips: the same allowance (the item's, else Stock Settings'
over_delivery_receipt_allowance) and the same role exempt from it (Stock Settings'
role_allowed_to_over_deliver_receive).

*A rejection is told.* When such a receipt is submitted, the order's owner and every Purchase
Manager get a notification naming what was rejected.
"""

import frappe
from frappe import _
from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
from frappe.utils import flt, get_url_to_form


def _rejected(doc):
	return [row for row in doc.get("items") or [] if flt(row.get("rejected_qty")) > 0]


def validate(doc, method=None):
	if doc.get("is_return"):
		return
	failed = doc.get("folt_inspection_status") == "Failed"
	if (_rejected(doc) or failed) and not (doc.get("folt_discrepancy_notes") or "").strip():
		frappe.throw(
			_("Say what was wrong (Discrepancy notes): this receipt rejects goods or failed inspection."),
			title=_("Discrepancy notes required"),
		)


def on_submit(doc, method=None):
	rejected = _rejected(doc)
	if doc.get("is_return") or not rejected:
		return
	orders = {row.purchase_order for row in doc.items if row.get("purchase_order")}
	recipients = set(frappe.get_all("Purchase Order", filters={"name": ["in", list(orders) or [""]]}, pluck="owner"))
	managers = frappe.get_all("Has Role", filters={"role": "Purchase Manager", "parenttype": "User"}, pluck="parent")
	recipients |= set(frappe.get_all("User", filters={"name": ["in", managers or [""]], "enabled": 1, "user_type": "System User"}, pluck="name"))
	recipients.discard("Administrator")
	recipients.discard(frappe.session.user)
	if not recipients:
		return
	what = ", ".join(f"{flt(r.rejected_qty):g} {r.uom or ''} {r.item_name or r.item_code}".strip() for r in rejected)
	enqueue_create_notification(
		sorted(recipients),
		{
			"type": "Alert",
			"subject": _("{0}: {1} rejected on receipt from {2}").format(doc.name, what, doc.supplier_name or doc.supplier),
			"email_content": _("{0} rejected {1}. {2}").format(frappe.bold(doc.name), what, doc.get("folt_discrepancy_notes") or ""),
			"document_type": doc.doctype,
			"document_name": doc.name,
			"from_user": frappe.session.user,
			"link": get_url_to_form(doc.doctype, doc.name),
		},
	)


def before_submit(doc, method=None):
	if doc.get("is_return"):
		return
	exempt_role = frappe.db.get_single_value("Stock Settings", "role_allowed_to_over_deliver_receive")
	if exempt_role and exempt_role in frappe.get_roles():
		return
	global_allowance = flt(frappe.db.get_single_value("Stock Settings", "over_delivery_receipt_allowance"))
	for row in doc.get("items") or []:
		if not row.get("purchase_order_item") or frappe.get_cached_value("Item", row.item_code, "is_stock_item"):
			continue  # stock items: ERPNext's own check
		ordered, received = frappe.db.get_value("Purchase Order Item", row.purchase_order_item, ["qty", "received_qty"]) or (0, 0)
		allowance = flt(frappe.get_cached_value("Item", row.item_code, "over_delivery_receipt_allowance")) or global_allowance
		limit = flt(ordered) * (1 + allowance / 100)
		if flt(received) + flt(row.received_qty) > limit + 0.0001:
			frappe.throw(
				_("Row {0}: {1} receives {2} of {3}, but the order allows {4} and {5} have been received already.").format(
					row.idx, row.item_name or row.item_code, flt(row.received_qty), row.purchase_order, flt(limit), flt(received)
				),
				title=_("More than was ordered"),
			)
