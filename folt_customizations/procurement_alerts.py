"""Procurement reminders (ADR-008, phase 7): the daily run, and a bid's arrival.

Daily (scheduler), for what falls due within FoLT Procurement Settings' reminder_days:

  an RFQ's quotation deadline          -> its owner
  an order's delivery (required-by)    -> its owner and the Purchase Managers
  a supplier invoice's due date        -> its owner and the Accounts Managers

Each is told when it comes into the window, the day after it is overdue, and every seventh day it
stays overdue -- not every morning. A delivery is overdue while the order is not fully received; an
invoice while it has money outstanding (ERPNext's own per_received and outstanding_amount).

On submit, a supplier quotation tells the owner of each RFQ it answers that a bid has arrived.

Everything goes to the bell (Notification Log, type Alert), and as email where the person's
notification settings send it. SMS is not sent: FoLT has no SMS provider configured.
"""

import frappe
from frappe import _
from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
from frappe.utils import add_days, cint, date_diff, get_url_to_form, getdate, nowdate


def _lead_days() -> int:
	if not frappe.db.exists("DocType", "FoLT Procurement Settings"):
		return 3
	value = frappe.db.get_single_value("FoLT Procurement Settings", "reminder_days")
	return cint(value) if value is not None else 3


def _role_users(role):
	users = frappe.get_all("Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent")
	return set(frappe.get_all("User", filters={"name": ["in", users or [""]], "enabled": 1, "user_type": "System User"}, pluck="name")) - {"Administrator"}


def due_today(due, today, lead) -> str | None:
	"""Whether a date is reminded today: 'soon' when it enters the window, 'overdue' the day after
	and every seventh day after that; None otherwise."""
	if not due:
		return None
	days = date_diff(getdate(due), today)
	if days == lead:
		return "soon"
	if days < 0 and (-days == 1 or (-days - 1) % 7 == 0):
		return "overdue"
	return None


def _tell(users, doc_type, name, subject, body):
	users = sorted(u for u in users if u)
	if not users:
		return 0
	enqueue_create_notification(users, {"type": "Alert", "subject": subject, "email_content": body, "document_type": doc_type,
	                                     "document_name": name, "from_user": "Administrator", "link": get_url_to_form(doc_type, name)})
	return len(users)


def daily(today=None):
	"""The morning's procurement reminders. Returns how many were sent, per kind (for the e2e)."""
	today = getdate(today or nowdate())
	lead = _lead_days()
	sent = {"rfq": 0, "delivery": 0, "invoice": 0}

	for r in frappe.get_all("Request for Quotation", filters={"docstatus": 1, "folt_quotation_deadline": ["is", "set"]}, fields=["name", "owner", "folt_quotation_deadline"]):
		if due_today(r.folt_quotation_deadline, today, lead) == "soon":
			sent["rfq"] += _tell({r.owner}, "Request for Quotation", r.name, _("{0} closes for quotations on {1}").format(r.name, frappe.format(r.folt_quotation_deadline, {"fieldtype": "Datetime"})),
			                     _("Bids dated after the deadline are refused. Chase the suppliers who have not quoted."))

	managers = _role_users("Purchase Manager")
	for o in frappe.get_all("Purchase Order", filters={"docstatus": 1, "per_received": ["<", 100], "status": ["not in", ["Closed", "On Hold", "Completed"]],
	                                                   "schedule_date": ["between", [add_days(today, -400), add_days(today, lead)]]},
	                        fields=["name", "owner", "supplier_name", "schedule_date"]):
		kind = due_today(o.schedule_date, today, lead)
		if kind == "soon":
			sent["delivery"] += _tell({o.owner}, "Purchase Order", o.name, _("{0} from {1} is due by {2}").format(o.name, o.supplier_name, frappe.format(o.schedule_date, {"fieldtype": "Date"})),
			                          _("Delivery is due in {0} days and the order is not yet fully received.").format(lead))
		elif kind == "overdue":
			sent["delivery"] += _tell({o.owner} | managers, "Purchase Order", o.name, _("{0} from {1} is overdue").format(o.name, o.supplier_name),
			                          _("It was due by {0} and is not yet fully received.").format(frappe.format(o.schedule_date, {"fieldtype": "Date"})))

	accounts = _role_users("Accounts Manager")
	for p in frappe.get_all("Purchase Invoice", filters={"docstatus": 1, "outstanding_amount": [">", 0], "due_date": ["between", [add_days(today, -400), add_days(today, lead)]]},
	                        fields=["name", "owner", "supplier_name", "due_date", "outstanding_amount", "currency"]):
		kind = due_today(p.due_date, today, lead)
		if kind:
			sent["invoice"] += _tell({p.owner} | (accounts if kind == "overdue" else set()), "Purchase Invoice", p.name,
			                         (_("{0} to {1} falls due on {2}") if kind == "soon" else _("{0} to {1} is overdue (due {2})")).format(
				                         p.name, p.supplier_name, frappe.format(p.due_date, {"fieldtype": "Date"})),
			                         _("{0} {1} is outstanding.").format(p.currency, frappe.format(p.outstanding_amount, {"fieldtype": "Currency"})))
	return sent


def bid_arrived(doc, method=None):
	"""Supplier Quotation on_submit: the RFQ's owner hears that a bid is in."""
	rfqs = {r.request_for_quotation for r in doc.get("items") or [] if r.get("request_for_quotation")}
	for rfq in rfqs:
		owner = frappe.db.get_value("Request for Quotation", rfq, "owner")
		if owner and owner != frappe.session.user:
			_tell({owner}, "Supplier Quotation", doc.name, _("A bid from {0} has arrived on {1}").format(doc.supplier_name or doc.supplier, rfq),
			      _("{0} quoted {1} {2}.").format(doc.supplier_name or doc.supplier, doc.currency, frappe.format(doc.grand_total, {"fieldtype": "Currency"})))
