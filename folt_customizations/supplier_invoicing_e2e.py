"""End-to-end check of the supplier's side after the order: sending the invoice, and printing.

Runs FoLT's competitive-bidding workflow up to the issued LPO (procurement_sop_e2e's own steps),
then, as a portal login of the winning supplier:

  - the issued order is offered for invoicing, with each line's quantity still to invoice
  - an invoice is refused without its number, with a future date, without a copy attached, for
    more than was ordered, and for somebody else's order
  - a good one lands as a DRAFT Purchase Invoice made from the order (the order's price, not the
    supplier's typing), stamped with who sent it, the copy attached, owned by the supplier login
  - the same quantity cannot be invoiced twice while the first is being checked, nor the same
    invoice number sent again
  - the Finance Assistant forwards it (the copy and number are already there) and the Head of
    Finance approves it; the supplier sees it go from "received" to "approved"
  - the supplier prints their RFQ, quotation, LPO and invoice, and nobody else's

The documents stay as an example; procurement_sop_e2e's teardown removes them on the next run.

    bench --site <site> execute folt_customizations.supplier_invoicing_e2e.run
"""

import io

import frappe
from frappe.utils import add_days, nowdate

from folt_customizations import procurement_portal as pp
from folt_customizations import procurement_sop_e2e as sop
from folt_customizations import supplier_portal as sp
from folt_customizations.procurement_e2e_support import _blank_pdf

SUPPLIER_LOGIN = "e2e.sop.supplier@example.com"
OTHER_LOGIN = "e2e.sop.other.supplier@example.com"

check, expect_throw, as_user = sop.check, sop.expect_throw, sop.as_user


class _Upload:
	"""A stand-in for werkzeug's FileStorage, which is all submit_invoice reads off the request."""

	def __init__(self, filename, content):
		self.filename, self.stream = filename, io.BytesIO(content)


def _with_file(filename, content, fn):
	original = sp._uploaded_file

	def fake():
		frappe.request = frappe._dict(files={"file": _Upload(filename, content)} if filename else {})
		try:
			return original()
		finally:
			frappe.request = None

	sp._uploaded_file = fake
	try:
		return fn()
	finally:
		sp._uploaded_file = original


def _portal_login(email, supplier):
	if not frappe.db.exists("User", email):
		frappe.get_doc({"doctype": "User", "email": email, "first_name": "E2E SOP Supplier", "user_type": "Website User", "send_welcome_email": 0}).insert(ignore_permissions=True)
	sp.grant_portal_access(supplier, email)


def teardown():
	sop.teardown()
	for email in (SUPPLIER_LOGIN, OTHER_LOGIN):
		if frappe.db.exists("User", email):
			frappe.delete_doc("User", email, force=True, ignore_permissions=True)
	frappe.db.commit()


def run():
	sop.PASS.clear()
	sop.FAIL.clear()
	print("\nSupplier portal — invoicing an issued order, and printing\n")
	teardown()
	sop.setup()
	try:
		mr = sop.initiation()
		rfq = sop.step1_rfq(mr)
		bids = sop.step2_quotations(rfq)
		evaluation, winner = sop.step3_committee(rfq, bids)
		order = sop.step4_lpo(evaluation, winner)
		invoicing(order, rfq, winner)
	finally:
		frappe.set_user("Administrator")
		frappe.db.commit()
	print(f"\n  {len(sop.PASS)} passed, {len(sop.FAIL)} failed")
	for label in sop.FAIL:
		print(f"    FAIL  {label}")
	return {"passed": len(sop.PASS), "failed": len(sop.FAIL), "failures": list(sop.FAIL)}


def invoicing(order, rfq, winner):
	print("\n--- the supplier invoices the issued order on the portal ---")
	supplier = frappe.db.get_value("Purchase Order", order, "supplier")
	other = next(s for s in sop.BIDDERS if s != supplier)
	_portal_login(SUPPLIER_LOGIN, supplier)
	_portal_login(OTHER_LOGIN, other)
	frappe.db.commit()

	view = as_user(SUPPLIER_LOGIN, sp.portal_overview)
	mine = next((o for o in view["orders"] if o["name"] == order), None)
	check("the issued order is offered for invoicing", mine and mine["can_invoice"], mine and mine["status"])
	form = as_user(SUPPLIER_LOGIN, lambda: sp.invoice_form(order))
	line = form["lines"][0]
	check("each line says how much is left to invoice, at the order's price", line["to_invoice"] == 5 and line["rate"] == sop.BIDDERS[supplier], line)
	expect_throw("another supplier cannot open it", lambda: as_user(OTHER_LOGIN, lambda: sp.invoice_form(order)), frappe.PermissionError)

	# Delivered and received first, as the workflow orders it (an invoice ahead of the receipt is
	# accepted too, and the three-way match flags it for the approver).
	made = as_user(sop.RECEIVER, lambda: pp.lookup("erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt", {"source_name": order}))
	receipt = sop._clean(made)
	receipt.update({"posting_date": nowdate(), "supplier_delivery_note": "BT-DN-0042"})
	pr = as_user(sop.RECEIVER, lambda: pp.save("Purchase Receipt", receipt))["name"]
	as_user(sop.RECEIVER, lambda: pp.act("Purchase Receipt", pr, "submit"))

	pdf = _blank_pdf()
	good = [{"po_detail": line["po_detail"], "qty": 5}]

	def send(bill_no="BT-INV-0042", bill_date=None, lines=None, filename="BT-INV-0042.pdf", content=pdf, user=SUPPLIER_LOGIN):
		return _with_file(filename, content, lambda: as_user(user, lambda: sp.submit_invoice(order, bill_no, bill_date or nowdate(), frappe.as_json(lines or good))))

	expect_throw("an invoice needs its number", lambda: send(bill_no="  "))
	expect_throw("its date cannot be in the future", lambda: send(bill_date=add_days(nowdate(), 3)))
	expect_throw("it needs the invoice attached", lambda: send(filename=None))
	expect_throw("as a PDF or an image", lambda: send(filename="invoice.exe"))
	expect_throw("it cannot bill more than was ordered", lambda: send(lines=[{"po_detail": line["po_detail"], "qty": 6}]))
	expect_throw("nor bill somebody else's order", lambda: send(user=OTHER_LOGIN), frappe.PermissionError)

	sent = send()
	pi = frappe.get_doc("Purchase Invoice", sent["name"])
	check("it lands as a draft invoice made from the order", pi.docstatus == 0 and pi.workflow_state == "Draft" and pi.items[0].purchase_order == order, f"{pi.name} {pi.workflow_state}")
	check("at the order's price, for what was billed", pi.items[0].rate == sop.BIDDERS[supplier] and pi.items[0].qty == 5)
	check("stamped with who sent it, and theirs", pi.folt_submitted_by_supplier == SUPPLIER_LOGIN and pi.owner == SUPPLIER_LOGIN and pi.bill_no == "BT-INV-0042")
	check("and it matches the order and the receipt", pi.folt_match_status == "Matched", pi.folt_match_notes)
	check("with the invoice attached", frappe.db.exists("File", {"attached_to_doctype": "Purchase Invoice", "attached_to_name": pi.name}))
	expect_throw("the same quantity cannot be invoiced twice", lambda: send(bill_no="BT-INV-0043"))
	expect_throw("nor the same invoice number sent again", lambda: send(lines=[{"po_detail": line["po_detail"], "qty": 1}]))
	check("the order is no longer offered for invoicing", not as_user(SUPPLIER_LOGIN, lambda: sp.invoice_form(order))["can_invoice"])
	status = lambda: next(i["status"] for i in as_user(SUPPLIER_LOGIN, sp.portal_overview)["invoices"] if i["name"] == pi.name)  # noqa: E731
	check("the supplier sees it received", status() == "received")
	check("and never another supplier's invoices", all(i["supplier"] == supplier for i in as_user(SUPPLIER_LOGIN, sp.portal_overview)["invoices"]))

	print("\n--- FoLT reviews and approves it ---")
	as_user(sop.FIN_ASSISTANT, lambda: pp.act("Purchase Invoice", pi.name, "transition", "Submit for approval"))
	check("the Finance Assistant forwards it as sent", frappe.db.get_value("Purchase Invoice", pi.name, "workflow_state") == "Pending Approval")
	check("the supplier sees it being approved", status() == "approval")
	as_user(sop.HOF, lambda: pp.act("Purchase Invoice", pi.name, "transition", "Approve"))
	check("approved, it posts and the supplier sees it awaiting payment", frappe.db.get_value("Purchase Invoice", pi.name, "docstatus") == 1 and status() == "approved")

	print("\n--- the supplier prints their documents ---")
	quotation = winner

	def pdf_of(doctype, name, user=SUPPLIER_LOGIN):
		frappe.local.response = frappe._dict()
		as_user(user, lambda: sp.document_pdf(doctype, name))
		return frappe.local.response.get("filecontent") or b""

	for doctype, name in (("Purchase Order", order), ("Purchase Invoice", pi.name), ("Supplier Quotation", quotation), ("Request for Quotation", rfq)):
		body = pdf_of(doctype, name)
		check(f"prints their {doctype}", body.startswith(b"%PDF-"), f"{len(body)} bytes")
	expect_throw("not another supplier's order", lambda: pdf_of("Purchase Order", order, OTHER_LOGIN), frappe.PermissionError)
	expect_throw("not FoLT's internal documents", lambda: pdf_of("Procurement Committee Evaluation", frappe.db.get_value("Purchase Order", order, "folt_committee_evaluation")), frappe.PermissionError)
	frappe.local.response = frappe._dict()
