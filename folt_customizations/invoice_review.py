"""The Finance Assistant's review of a supplier's invoice (procurement workflow, step 5).

FoLT's procurement workflow: once goods or services are delivered the supplier submits an invoice,
and "the Finance Assistant reviews the invoice for accuracy and completeness before forwarding it
for approval". A Purchase Invoice against a purchase order is that invoice; forwarding it is its
workflow's `Draft -> Pending Approval` move. On that move this module asks for what a review is:

  * the person forwarding holds the Finance Assistant role;
  * the supplier's own invoice number and date are on it (bill_no, bill_date);
  * the supplier's invoice itself is attached, so the approver approves the document, not a retyping;

and it records who reviewed it and when (folt_reviewed_by / folt_reviewed_on). The approval that
follows must be somebody else's: the reviewer cannot also approve. Accuracy against the order and
the receipt is the three-way match (match.py), which runs on the same save and stops a quiet
approval of a discrepancy.

Only invoices against an order are held to this: a bill raised by Finance with no order behind it
is not a supplier's invoice arriving from the procurement chain, and Finance's own edge governs it.
Administrator is exempt (patches, fixtures, a console correction).
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

REVIEWER_ROLE = "Finance Assistant"
REVIEW_FIELDS = ("folt_reviewed_by", "folt_reviewed_on")


def is_procurement_invoice(doc) -> bool:
	return any(row.get("purchase_order") for row in doc.get("items") or [])


def validate(doc, method=None):
	if doc.get("is_return") or not doc.meta.has_field("folt_reviewed_by"):
		return
	from frappe.model.workflow import get_workflow, get_workflow_name

	before = doc.get_doc_before_save()
	# Written by the system only: whatever a client sent is put back to what was stored.
	for field in REVIEW_FIELDS:
		doc.set(field, before.get(field) if before else None)

	if not get_workflow_name(doc.doctype) or not is_procurement_invoice(doc):
		return
	wf = get_workflow(doc.doctype)
	field = wf.workflow_state_field
	was, now = (before.get(field) if before else None), doc.get(field)
	if was == now:
		return
	first = wf.states[0].state
	target = next((s for s in wf.states if s.state == now), None)

	if now == first:
		# Sent back: the next forward is a fresh review.
		for f in REVIEW_FIELDS:
			doc.set(f, None)
		return

	user = frappe.session.user
	if was == first and target and int(target.doc_status or 0) == 0:
		_require_review(doc, user)
		doc.folt_reviewed_by = user
		doc.folt_reviewed_on = now_datetime()
	elif target and int(target.doc_status or 0) == 1 and user != "Administrator":
		if doc.get("folt_reviewed_by") and doc.folt_reviewed_by == user:
			frappe.throw(
				_("You reviewed and forwarded {0}; its approval has to be somebody else's.").format(doc.name),
				title=_("Reviewer cannot approve"),
			)


def _require_review(doc, user):
	if user != "Administrator" and REVIEWER_ROLE not in frappe.get_roles(user):
		frappe.throw(
			_("A supplier's invoice is forwarded for approval by the {0}, after checking it for accuracy and completeness.").format(
				frappe.bold(REVIEWER_ROLE)
			),
			title=_("Finance Assistant review"),
		)
	missing = []
	if not (doc.get("bill_no") or "").strip():
		missing.append(_("the supplier's invoice number"))
	if not doc.get("bill_date"):
		missing.append(_("the supplier's invoice date"))
	if not frappe.db.exists("File", {"attached_to_doctype": doc.doctype, "attached_to_name": doc.name}):
		missing.append(_("the supplier's invoice, attached"))
	if missing:
		frappe.throw(
			_("{0} is not complete. Before forwarding it for approval add: {1}.").format(doc.name, ", ".join(missing)),
			title=_("Invoice incomplete"),
		)
