"""Requests for quotation and the bids against them (ADR-008, phase 3).

An RFQ may carry a quotation deadline (folt_quotation_deadline). Two rules hang off it:

*The deadline is after the RFQ is sent.* An RFQ dated today cannot close yesterday.

*A bid dated after the deadline is refused.* A Supplier Quotation is dated the day it is made --
the supplier portal stamps today (supplier_portal.save_quotation), a buyer keying a paper bid
dates it the day it was received -- so its own date against the deadline of every RFQ it answers
is the test, on every save. A bid that arrived on time and is keyed later is dated when it
arrived, and stands.
"""

import frappe
from frappe import _
from frappe.utils import get_datetime, getdate


def validate_rfq(doc, method=None):
	deadline = doc.get("folt_quotation_deadline")
	if deadline and doc.get("transaction_date") and get_datetime(deadline).date() < getdate(doc.transaction_date):
		frappe.throw(_("The quotation deadline cannot be before the RFQ's own date."), title=_("Deadline"))


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
