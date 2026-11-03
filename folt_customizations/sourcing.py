"""Requests for quotation and the bids against them (ADR-008, phase 3).

An RFQ may carry a quotation deadline (folt_quotation_deadline). Two rules hang off it:

*The deadline is after the RFQ is sent.* An RFQ dated today cannot close yesterday.

*A bid dated after the deadline is refused.* A Supplier Quotation is dated the day it is made --
the supplier portal stamps today (supplier_portal.save_quotation), a buyer keying a paper bid
dates it the day it was received -- so its own date against the deadline of every RFQ it answers
is the test, on every save. A bid that arrived on time and is keyed later is dated when it
arrived, and stands.

Two more rules come from FoLT's procurement workflow (competitive bidding, before step 1 and step
1 itself), and both bite when the RFQ is sent (submitted):

*Procurement starts from an approved requisition.* "The procurement process begins upon receipt of
the approved budget and Activity Concept Note from the Program Department. Once reviewed and
approved by the Head of Finance and the Head of Programs, the procurement process is initiated."
That approval is the purchase requisition's (Material Request, FoLT Purchase Requisition Approval:
Head of Programs then Head of Finance), so every line of an RFQ must come from an approved
requisition for purchase -- which is what ERPNext's own "Request for Quotation" button on the
requisition writes (RFQ Item.material_request).

*Quotations are requested from pre-qualified suppliers.* A supplier whose pre-qualification has
lapsed (folt_qualified_until in the past) or who is disabled cannot be sent the RFQ.
"""

import frappe
from frappe import _
from frappe.utils import get_datetime, getdate


def validate_rfq(doc, method=None):
	deadline = doc.get("folt_quotation_deadline")
	if deadline and doc.get("transaction_date") and get_datetime(deadline).date() < getdate(doc.transaction_date):
		frappe.throw(_("The quotation deadline cannot be before the RFQ's own date."), title=_("Deadline"))


def before_submit_rfq(doc, method=None):
	"""Request for Quotation before_submit: an approved requisition behind every line, and only
	pre-qualified suppliers."""
	_require_approved_requisition(doc)
	_require_qualified_suppliers(doc)


def _require_approved_requisition(doc):
	from frappe.model.workflow import get_workflow_name

	unlinked = [str(row.idx) for row in doc.get("items") or [] if not row.get("material_request")]
	if unlinked:
		frappe.throw(
			_("Row(s) {0} of {1} come from no purchase requisition. Procurement starts from a requisition approved by the Head of Programs and the Head of Finance: raise the RFQ from the approved requisition.").format(
				", ".join(unlinked), doc.name or _("this RFQ")
			),
			title=_("No approved requisition"),
		)
	workflow = get_workflow_name("Material Request")
	for name in sorted({row.material_request for row in doc.items}):
		mr = frappe.db.get_value(
			"Material Request", name, ["docstatus", "status", "material_request_type", "workflow_state"], as_dict=True
		)
		approved = (
			mr
			and mr.docstatus == 1
			and mr.material_request_type == "Purchase"
			and mr.status not in ("Stopped", "Cancelled")
			and (not workflow or mr.workflow_state == "Approved")
		)
		if not approved:
			frappe.throw(
				_("{0} is not an approved purchase requisition (it is {1}). Quotations are requested only once the Head of Programs and the Head of Finance have approved it.").format(
					frappe.bold(name), (mr and (mr.workflow_state or mr.status)) or _("missing")
				),
				title=_("Requisition not approved"),
			)


def _require_qualified_suppliers(doc):
	from folt_customizations.supplier import qualification_expiry

	for row in doc.get("suppliers") or []:
		if not row.get("supplier"):
			continue
		if frappe.db.get_value("Supplier", row.supplier, "disabled"):
			frappe.throw(_("{0} is disabled and cannot be asked to quote.").format(frappe.bold(row.supplier)), title=_("Supplier not pre-qualified"))
		lapsed = qualification_expiry(row.supplier)
		if lapsed:
			frappe.throw(
				_("{0}'s pre-qualification lapsed on {1}. Only pre-qualified suppliers are asked to quote: renew it or remove the supplier.").format(
					frappe.bold(row.supplier), frappe.format(lapsed, {"fieldtype": "Date"})
				),
				title=_("Supplier not pre-qualified"),
			)


def validate_quotation(doc, method=None):
	if not doc.get("transaction_date"):
		return
	rfqs = {row.request_for_quotation for row in doc.get("items") or [] if row.get("request_for_quotation")}
	for rfq in rfqs:
		deadline = frappe.db.get_value("Request for Quotation", rfq, "folt_quotation_deadline")
		if deadline and getdate(doc.transaction_date) > get_datetime(deadline).date():
			frappe.throw(
				_("{0} closed for quotations on {1}. A bid dated {2} is late.").format(
					frappe.bold(rfq), frappe.format(get_datetime(deadline), {"fieldtype": "Datetime"}), frappe.format(doc.transaction_date, {"fieldtype": "Date"})
				),
				title=_("Quotation deadline passed"),
			)
