import frappe
from frappe import _
from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
from frappe.utils import add_days, flt, get_url_to_form, getdate, today
from frappe.utils.user import get_users_with_role

ADVANCE = "Employee Advance"

# The Float Request Form carries its own undertaking -- "Any unaccounted floats beyond 3 days
# after a trip should automatically be recovered from my salary or benefits without further
# notice" -- and until now that was a line of print on a form nobody could act on. Step 4 of
# the finance workflow (accountability and filing) is where it bites, so the deadline becomes a
# date on the advance and a daily sweep, not a warning in a footer.
RETIREMENT_DAYS = 3

# ERPNext already derives Employee Advance.status from the vouchers posted against the advance:
# a payment makes it Paid or Partially Paid, a retirement claim makes it Claimed, a refund of
# the balance Returned. Asking a Finance Assistant to *also* click "Record Disbursement" would
# be retyping what the ledger already knows, which is the opposite of streamlining -- so the
# post-approval states are derived from that status. The manual transitions stay in the
# workflow so a state can be corrected by hand, but on the normal path nobody uses them.
STATE_FROM_STATUS = {
	"Paid": "Disbursed",
	"Partially Paid": "Disbursed",
	"Claimed": "Accounted",
	"Returned": "Accounted",
	"Partly Claimed and Returned": "Accounted",
}

APPROVED = "Approved"
DISBURSED = "Disbursed"
OVERDUE = "Overdue"
ACCOUNTED = "Accounted"
CLOSED = "Closed"

# The only states this module will overwrite. Requested, Checked and Rejected all precede
# disbursement, and Closed is the Head of Finance's deliberate sign-off that the float is
# finished with -- an automated sweep must not undo a human decision, so neither end is derived.
DERIVED_STATES = (APPROVED, DISBURSED, OVERDUE, ACCOUNTED)

ESCALATION_ROLE = "Head of Finance"


def set_retirement_deadline(doc, method=None):
	"""Stamp the date by which this float has to be accounted for (Employee Advance.validate).

	Counted from the end of the activity the float is for, because that is what the form's
	undertaking says -- three days after the trip, not three days after the money arrived.
	Falls back to the advance's own posting date when the project carries no end date.
	"""
	if not doc.meta.has_field("folt_retire_by"):
		return

	doc.folt_retire_by = _retirement_deadline(doc.get("folt_project"), doc.posting_date)


def _retirement_deadline(project, posting_date):
	activity_end = None
	if project:
		activity_end = frappe.db.get_value("Project", project, "expected_end_date")

	return add_days(activity_end or posting_date, RETIREMENT_DAYS)


def sync_from_voucher(doc, method=None):
	"""A Payment Entry or Journal Entry has funded, or refunded, one or more floats.

	Hooked on both because either can post against an advance: ERPNext routes every voucher
	through `update_voucher_outstanding`, which recomputes Employee Advance.paid_amount and
	status from the payment ledger. That happens inside the submitting document's own
	transaction, so the sync is deferred until after the commit -- see `_queue`.
	"""
	for advance in _referenced_advances(doc):
		_queue(advance)


def sync_from_claim(doc, method=None):
	"""A retirement claim has been filed, approved or cancelled against a float."""
	for row in doc.get("advances") or []:
		if row.employee_advance:
			_queue(row.employee_advance)


def _referenced_advances(doc) -> set[str]:
	"""Advances referenced by a voucher, whichever child table it keeps them in."""
	rows = (doc.get("references") or []) + (doc.get("accounts") or [])
	return {
		row.reference_name
		for row in rows
		if row.get("reference_name")
		and (row.get("reference_doctype") or row.get("reference_type")) == ADVANCE
	}


def _queue(advance: str):
	# `enqueue_after_commit` is the point of doing this in a job at all. Our doc_events run
	# alongside those of erpnext and hrms with no ordering guarantee between apps, so reading
	# `status` inline would race the very update we are reacting to. After the commit it is
	# settled, whoever wrote it.
	frappe.enqueue(
		"folt_customizations.float_lifecycle.sync_float_state",
		queue="short",
		enqueue_after_commit=True,
		advance=advance,
	)


def sync_float_state(advance: str):
	"""Move a float's workflow state to match what the ledger says has happened to it."""
	current = frappe.db.get_value(
		ADVANCE,
		advance,
		["docstatus", "status", "workflow_state", "folt_retire_by", "claimed_amount"],
		as_dict=True,
	)
	if not current or current.docstatus != 1:
		return

	# A retirement claim is what makes a float accounted for, and ERPNext's own status does not
	# say so on its own: it only reaches Claimed when the claim covers the *whole* advance. The
	# worked case retired 262,004 against a 262,000 float, and a float retired for less than it
	# received -- the ordinary outcome -- leaves a balance and a status still reading Paid. The
	# accountability document exists either way, so that is what the state follows; settling the
	# balance is the Head of Finance's Close Float decision, not this.
	if flt(current.claimed_amount) > 0:
		target = ACCOUNTED
	else:
		target = STATE_FROM_STATUS.get(current.status)

	if not target:
		return

	# A float that is out and past its deadline is Overdue rather than merely Disbursed. Doing
	# it here as well as in the daily sweep means a late disbursement lands in the right state
	# immediately instead of looking current until the next night.
	if target == DISBURSED and _is_overdue(current.folt_retire_by):
		target = OVERDUE

	_apply(advance, current.workflow_state, target, _("derived from advance status {0}").format(current.status))


def flag_overdue_floats():
	"""Daily sweep: floats that are out, past their deadline and still not accounted for.

	This is the automated half of the form's three-day undertaking. Recovery from salary stays
	a human decision (hrms already builds the Additional Salary for it); what the sweep owes
	the Head of Finance is that the float stops looking current the day it stops being current.
	"""
	floats = frappe.get_all(
		ADVANCE,
		filters={"docstatus": 1, "workflow_state": DISBURSED},
		fields=["name", "employee_name", "advance_amount", "folt_project", "posting_date", "folt_retire_by"],
	)

	flagged = []
	for advance in floats:
		# Recomputed rather than trusted: the activity's end date is often set on the Project
		# after the float has already been approved and submitted, and validate() does not run
		# again to pick it up.
		deadline = _retirement_deadline(advance.folt_project, advance.posting_date)
		if deadline != advance.folt_retire_by:
			frappe.db.set_value(ADVANCE, advance.name, "folt_retire_by", deadline, update_modified=False)
			advance.folt_retire_by = deadline

		if not _is_overdue(deadline):
			continue

		if _apply(advance.name, DISBURSED, OVERDUE, _("unaccounted for since {0}").format(deadline)):
			flagged.append(advance)

	if flagged:
		_notify_overdue(flagged)

	return [advance.name for advance in flagged]


def _is_overdue(deadline) -> bool:
	return bool(deadline) and getdate(deadline) < getdate(today())


def _apply(advance: str, current: str, target: str, reason: str) -> bool:
	"""Write a derived state, and say on the document why it moved.

	`db.set_value` rather than a workflow transition on purpose: there is no acting user to
	attribute the move to, and `apply_workflow` would check that user's roles. The comment is
	what keeps the change auditable -- a state that changes with nothing in the timeline
	explaining it is worse than one nobody updated.
	"""
	if current not in DERIVED_STATES or current == target:
		return False

	frappe.db.set_value(ADVANCE, advance, "workflow_state", target, update_modified=False)
	frappe.get_doc(ADVANCE, advance).add_comment(
		"Workflow", _("{0} &rarr; {1} ({2})").format(current, target, reason)
	)
	return True


def _notify_overdue(floats):
	recipients = sorted(get_users_with_role(ESCALATION_ROLE))
	if not recipients:
		# Nobody holds the role that is supposed to chase these, which is worth a log entry
		# rather than a silent return: the sweep did its job and the escalation went nowhere.
		frappe.log_error(
			title="Overdue floats with nobody to escalate to",
			message=_("{0} float(s) went overdue and no user holds the {1} role.").format(
				len(floats), ESCALATION_ROLE
			),
		)
		return

	for advance in floats:
		enqueue_create_notification(
			recipients,
			{
				"type": "Alert",
				"subject": _("Float {0} is overdue for accountability").format(advance.name),
				"email_content": _(
					"{0} held by {1} was due to be accounted for by {2} and has not been retired."
				).format(
					frappe.bold(frappe.format_value(advance.advance_amount, {"fieldtype": "Currency"})),
					advance.employee_name,
					frappe.format_value(advance.folt_retire_by, {"fieldtype": "Date"}),
				),
				"document_type": ADVANCE,
				"document_name": advance.name,
				"link": get_url_to_form(ADVANCE, advance.name),
				"from_user": frappe.session.user,
			},
		)
