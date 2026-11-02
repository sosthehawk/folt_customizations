"""Three-way match on a supplier invoice (ADR-008, phase 6): what was ordered (Purchase Order), what
was received (Purchase Receipt) and what is billed (Purchase Invoice), line by line.

Every figure is ERPNext's: each invoice line names its order line (po_detail) and receipt line
(pr_detail), and the order line carries what has been received against it (received_qty) and
billed (other submitted invoices' quantities). The match reads them and says where they disagree:

  price      the billed unit price differs from the order's by more than the tolerance
             (FoLT Procurement Settings.match_price_tolerance, percent; base currency)
  quantity   billed, with earlier invoices, more than has been received (or ordered, for a line
             the order has received nothing of yet)
  receipt    an order line billed with nothing received against it
  unordered  a line that names no order at all
  duplicate  another invoice from the same supplier carries the same supplier invoice number
  taxes      a different tax template from the order's, or a tax share of the net that differs
             from the order's by more than the tolerance
  charges    a charge on the invoice the order did not carry

The result is written on the invoice (folt_match_status, folt_match_notes) on every save. It does
not stop an invoice being prepared or sent for approval; it stops it being APPROVED quietly: an
invoice with discrepancies is approved only with the approver's reason
(folt_match_override_reason), parked the way a turn-down's reason is (workflow_access).
"""

import frappe
from frappe import _
from frappe.utils import flt

STATUS_MATCHED = "Matched"
STATUS_DISCREPANCIES = "Discrepancies"
STATUS_NO_ORDER = "No order"


def tolerance() -> float:
	if not frappe.db.exists("DocType", "FoLT Procurement Settings"):
		return 2.0
	value = frappe.db.get_single_value("FoLT Procurement Settings", "match_price_tolerance")
	return flt(value) if value is not None else 2.0


def _differs(a, b, pct):
	a, b = flt(a), flt(b)
	if not b:
		return bool(a)
	return abs(a - b) / abs(b) * 100 > pct + 1e-9


def compute(doc) -> dict:
	"""The match for an invoice document (saved or not). Returns {status, flags, tolerance}."""
	pct = tolerance()
	flags = []
	lines = [r for r in doc.get("items") or [] if r.get("item_code")]
	orders = {r.purchase_order for r in lines if r.get("purchase_order")}
	if not orders and not any(r.get("purchase_receipt") for r in lines):
		return {"status": STATUS_NO_ORDER, "flags": [], "tolerance": pct}

	def flag(kind, row, message, expected=None, actual=None):
		flags.append({"kind": kind, "line": row.idx if row is not None else None, "item": row.item_code if row is not None else None,
		              "message": message, "expected": expected, "actual": actual})

	for row in lines:
		if not row.get("po_detail"):
			flag("unordered", row, _("Line {0} ({1}) is not on any purchase order.").format(row.idx, row.item_code))
			continue
		po = frappe.db.get_value("Purchase Order Item", row.po_detail, ["parent", "qty", "base_rate", "received_qty", "conversion_factor"], as_dict=True)
		if not po:
			flag("unordered", row, _("Line {0}: its order line no longer exists.").format(row.idx))
			continue
		if _differs(row.get("base_rate"), po.base_rate, pct):
			flag("price", row, _("Line {0} ({1}) bills {2} a unit; {3} ordered it at {4}.").format(
				row.idx, row.item_code, flt(row.base_rate, 2), po.parent, flt(po.base_rate, 2)), flt(po.base_rate), flt(row.base_rate))
		billed_before = flt(frappe.db.sql(
			"""select coalesce(sum(i.qty), 0) from `tabPurchase Invoice Item` i join `tabPurchase Invoice` p on p.name = i.parent
			where i.po_detail = %s and p.docstatus = 1 and p.is_return = 0 and p.name != %s""", (row.po_detail, doc.name or ""))[0][0])
		received = flt(po.received_qty) / (flt(po.conversion_factor) or 1)
		billed = billed_before + flt(row.qty)
		if not received and not row.get("pr_detail"):
			flag("receipt", row, _("Line {0} ({1}) is billed, but nothing has been received against {2}.").format(row.idx, row.item_code, po.parent))
			if billed > flt(po.qty) + 1e-9:
				flag("quantity", row, _("Line {0} bills {1} in all; {2} ordered {3}.").format(row.idx, billed, po.parent, flt(po.qty)), flt(po.qty), billed)
		elif billed > received + 1e-9:
			flag("quantity", row, _("Line {0} ({1}) bills {2} in all; {3} has been received.").format(row.idx, row.item_code, billed, received), received, billed)

	if doc.get("bill_no") and doc.get("supplier"):
		dup = frappe.db.get_value("Purchase Invoice", {"supplier": doc.supplier, "bill_no": doc.bill_no, "docstatus": ["<", 2], "name": ["!=", doc.name or ""]}, "name")
		if dup:
			flag("duplicate", None, _("{0} already carries this supplier's invoice number {1}.").format(dup, doc.bill_no))

	for order in sorted(orders):
		po = frappe.db.get_value("Purchase Order", order, ["taxes_and_charges", "base_net_total", "base_total_taxes_and_charges"], as_dict=True)
		if not po:
			continue
		if (doc.get("taxes_and_charges") or None) != (po.taxes_and_charges or None):
			flag("taxes", None, _("The tax template is {0}; {1} used {2}.").format(doc.get("taxes_and_charges") or _("none"), order, po.taxes_and_charges or _("none")))
		po_share = flt(po.base_total_taxes_and_charges) / flt(po.base_net_total) * 100 if flt(po.base_net_total) else 0
		pi_share = flt(doc.get("base_total_taxes_and_charges")) / flt(doc.get("base_net_total")) * 100 if flt(doc.get("base_net_total")) else 0
		if abs(pi_share - po_share) > pct + 1e-9:
			flag("taxes", None, _("Taxes are {0}% of the net; on {1} they were {2}%.").format(flt(pi_share, 2), order, flt(po_share, 2)), flt(po_share, 2), flt(pi_share, 2))
		po_heads = set(frappe.get_all("Purchase Taxes and Charges", filters={"parent": order, "parenttype": "Purchase Order"}, pluck="account_head"))
		for tax in doc.get("taxes") or []:
			if tax.get("account_head") and tax.account_head not in po_heads and flt(tax.get("base_tax_amount")):
				flag("charges", None, _("{0} of {1} is charged to {2}, which {3} did not carry.").format(
					tax.get("description") or _("A charge"), flt(tax.base_tax_amount, 2), tax.account_head, order), None, flt(tax.base_tax_amount))

	return {"status": STATUS_DISCREPANCIES if flags else STATUS_MATCHED, "flags": flags, "tolerance": pct}


def validate(doc, method=None):
	"""Purchase Invoice validate: write the match, and gate a quiet approval of a discrepancy."""
	if doc.get("is_return") or not doc.meta.has_field("folt_match_status"):
		return
	result = compute(doc)
	doc.folt_match_status = result["status"]
	doc.folt_match_notes = "\n".join(f["message"] for f in result["flags"]) or None
	_approval_needs_reason(doc, result)


def _approval_needs_reason(doc, result):
	from frappe.model.workflow import get_workflow, get_workflow_name

	from folt_customizations.workflow_access import held_reason

	if result["status"] != STATUS_DISCREPANCIES or not get_workflow_name(doc.doctype):
		return
	wf = get_workflow(doc.doctype)
	before = doc.get_doc_before_save()
	if not before:
		return
	moving_to = doc.get(wf.workflow_state_field)
	target = next((s for s in wf.states if s.state == moving_to), None)
	if before.get(wf.workflow_state_field) == moving_to or not target or int(target.doc_status or 0) != 1:
		return
	reason = held_reason(doc.doctype, doc.name, frappe.db.get_value(doc.doctype, doc.name, "modified"))
	if not (reason or "").strip() and not (doc.get("folt_match_override_reason") or "").strip():
		frappe.throw(
			_("{0} does not match its order and receipt:\n{1}\nApprove it with your reason, or send it back.").format(doc.name, doc.folt_match_notes),
			title=_("Approving a discrepancy needs a reason"),
		)
	if (reason or "").strip():
		doc.folt_match_override_reason = reason.strip()
		doc.add_comment("Comment", _("Approved despite the match: {0}").format(reason.strip()))


def needs_reason(doc, next_state) -> bool:
	"""For a record's moves: an approval of an invoice with discrepancies asks for the reason."""
	if doc.doctype != "Purchase Invoice" or doc.get("folt_match_status") != STATUS_DISCREPANCIES:
		return False
	from frappe.model.workflow import get_workflow

	target = next((s for s in get_workflow(doc.doctype).states if s.state == next_state), None)
	return bool(target and int(target.doc_status or 0) == 1)
