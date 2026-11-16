"""FoLT's leave policy (HR policy: leave application and management), enforced on the server.

The policy has three rules, and HRMS on its own keeps none of them:

  1. NOTICE. A leave application is made at least NOTICE_DAYS (seven) days before its first day.
     `validate` refuses a shorter one -- unless it is marked as an emergency with a reason, which
     is the policy's own exception ("emergencies or other unforeseen circumstances, subject to
     management approval"). An emergency request goes to the same approver as any other, flagged,
     so the approval IS the management approval. The check runs when an application is made and
     when its first day is moved, never when it is decided: an approver answering on day three
     must not be told the applicant was late.

  2. OVERLAPS IN A DEPARTMENT. Nothing is refused: the policy asks departmental heads to "review
     and coordinate the dates based on staffing levels and operational requirements", which is a
     judgement. So the facts are put in front of the two people who make it -- the applicant
     before they send (`leave_check`) and the approver before they decide (`overlaps_for`): who
     else in the department is away or has asked to be on those days, and the most people the
     department would be missing on any one of them.

  3. APPROVAL BEFORE LEAVE. An application must name an approver (HRMS only asks for one in the
     browser), nobody approves their own (HR Settings' `prevent_self_leave_approval`, switched
     on by `apply_leave_policy`), and `remind_undecided` tells the approver and the applicant
     every morning about a request that starts within REMIND_DAYS and is still undecided.

Who sees what follows the team calendar: a colleague's overlap is a name, the dates and whether
it is approved -- never the leave type or the reason.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, date_diff, getdate, nowdate

from folt_customizations.bff_support import NOTICE_DAYS  # seven days; one definition for the form and the rule
REMIND_DAYS = 2
HR_ROLES = {"HR User", "HR Manager"}
LEAVE = "Leave Application"


# --- the rules -----------------------------------------------------------------------------------


def validate(doc, method=None):
	"""Leave Application.validate, after HRMS's own."""
	if doc.docstatus != 0 or doc.status in ("Rejected", "Cancelled"):
		return
	is_new = doc.is_new()

	if is_new or doc.has_value_changed("from_date"):
		short = date_diff(doc.from_date, nowdate()) < NOTICE_DAYS
		if not short:
			# Not an emergency by the policy's meaning, whatever was ticked.
			doc.folt_emergency = 0
			doc.folt_emergency_reason = None
		elif not cint(doc.folt_emergency):
			frappe.throw(
				_(
					"Leave is applied for at least {0} days before it starts. If this is an emergency, mark the request as an emergency and say why."
				).format(NOTICE_DAYS),
				title=_("Short notice"),
			)
		elif not (doc.folt_emergency_reason or "").strip():
			frappe.throw(_("Say what the emergency is, so the approver can decide."), title=_("Short notice"))

	if is_new and doc.status == "Open" and not doc.leave_approver:
		frappe.throw(
			_("All leave must be approved, and no leave approver is set for {0}. Ask HR to name one.").format(
				frappe.bold(doc.employee_name or doc.employee)
			),
			title=_("No leave approver"),
		)


def apply_leave_policy():
	"""after_install / after_migrate: the two HR Settings the policy depends on. Both are switched
	on, not merely defaulted -- with either off, leave can be taken without anyone else approving."""
	settings = frappe.get_single("HR Settings")
	wanted = {"leave_approver_mandatory_in_leave_application": 1, "prevent_self_leave_approval": 1}
	changed = [f for f, v in wanted.items() if settings.meta.has_field(f) and cint(settings.get(f)) != v]
	for field in changed:
		settings.set(field, wanted[field])
	if changed:
		settings.flags.ignore_permissions = True
		settings.save()
	return changed


# --- what the applicant and the approver are shown ----------------------------------------------


@frappe.whitelist(methods=["GET"])
def leave_check(from_date: str, to_date: str, employee: str | None = None) -> dict:
	"""Before a request is sent: how much notice it gives, and who else in the department is away.

	`employee` is HR recording leave for someone else (HR User / HR Manager only)."""
	start, end = getdate(from_date), getdate(to_date)
	if not start or not end or end < start:
		frappe.throw(_("The range ends before it starts."), title=_("Invalid range"))
	if employee:
		if frappe.session.user != "Administrator" and not HR_ROLES.intersection(frappe.get_roles()):
			frappe.throw(_("Only HR can record leave for someone else."), frappe.PermissionError)
		row = frappe.db.get_value("Employee", {"name": employee, "status": "Active"}, ["name", "department"], as_dict=True)
	else:
		row = frappe.db.get_value(
			"Employee", {"user_id": frappe.session.user, "status": "Active"}, ["name", "department"], as_dict=True
		)
	if not row:
		frappe.throw(_("There is no active employee record to check leave for."), frappe.DoesNotExistError)
	days_notice = date_diff(start, nowdate())
	return {
		"notice_days": NOTICE_DAYS,
		"days_notice": days_notice,
		"short_notice": days_notice < NOTICE_DAYS,
		**_coverage(row.name, row.department, start, end),
	}


@frappe.whitelist(methods=["GET", "POST"])
def overlaps_for(names: str | list) -> dict:
	"""For the approvals queue: each application's notice and department overlaps, by name.

	Leave Approver, HR User and HR Manager only, and only for applications the caller can read."""
	if frappe.session.user != "Administrator" and not ({"Leave Approver"} | HR_ROLES).intersection(frappe.get_roles()):
		frappe.throw(_("Only leave approvers and HR can see this."), frappe.PermissionError)
	names = frappe.parse_json(names) if isinstance(names, str) else names
	names = [n for n in (names or []) if isinstance(n, str)][:200]
	out = {}
	if not names:
		return out
	rows = frappe.get_list(
		LEAVE,
		filters={"name": ["in", names]},
		fields=["name", "employee", "department", "from_date", "to_date", "posting_date", "creation"],
		limit_page_length=0,
	)
	for row in rows:
		applied = getdate(row.posting_date or row.creation)
		days_notice = date_diff(row.from_date, applied)
		out[row.name] = {
			"notice_days": NOTICE_DAYS,
			"days_notice": days_notice,
			"short_notice": days_notice < NOTICE_DAYS,
			**_coverage(row.employee, row.department, getdate(row.from_date), getdate(row.to_date), exclude=row.name),
		}
	return out


def _coverage(employee, department, start, end, exclude=None) -> dict:
	"""Who else in `department` has leave (approved, or asked for) between two dates, and the day
	the department would be thinnest if this request went ahead."""
	empty = {"department": department, "headcount": 0, "overlaps": [], "peak_away": 0, "peak_date": None}
	if not department:
		return empty
	headcount = frappe.db.count("Employee", {"department": department, "status": "Active"})
	filters = {
		"department": department,
		"employee": ["!=", employee],
		"docstatus": ["<", 2],
		"status": ["in", ["Open", "Approved"]],
		"from_date": ["<=", end],
		"to_date": [">=", start],
	}
	if exclude:
		filters["name"] = ["!=", exclude]
	rows = frappe.get_all(
		LEAVE,
		filters=filters,
		fields=["name", "employee", "employee_name", "from_date", "to_date", "status"],
		order_by="from_date asc",
		limit_page_length=200,
	)
	overlaps = [
		{
			"name": r.name,
			"employee": r.employee,
			"employee_name": r.employee_name or r.employee,
			"from_date": str(r.from_date),
			"to_date": str(r.to_date),
			"status": r.status,
		}
		for r in rows
	]
	peak_away, peak_date = 0, None
	if rows:
		day = start
		while day <= end:
			away = len({r.employee for r in rows if getdate(r.from_date) <= day <= getdate(r.to_date)})
			if away > peak_away:
				peak_away, peak_date = away, day
			day = add_days(day, 1)
		peak_away += 1  # with the applicant
	return {
		"department": department,
		"headcount": headcount,
		"overlaps": overlaps,
		"peak_away": peak_away,
		"peak_date": str(peak_date) if peak_date else None,
	}


# --- approval before leave: the morning reminder -------------------------------------------------


def remind_undecided():
	"""Daily: a request that starts within REMIND_DAYS (or already has) and is still undecided.

	The approver is told it is waiting; the applicant is told it is not approved yet, which under
	the policy means they do not proceed on leave. Past requests are left once their last day has
	gone: by then it is HR's to tidy, not a reminder's."""
	from folt_customizations.leave_notifications import _notifications_enabled, _notify
	from folt_customizations.notifications import _active_users

	if not _notifications_enabled():
		return 0
	today = getdate(nowdate())
	rows = frappe.get_all(
		LEAVE,
		filters={
			"docstatus": 0,
			"status": "Open",
			"from_date": ["<=", add_days(today, REMIND_DAYS)],
			"to_date": [">=", today],
		},
		fields=["name", "employee", "employee_name", "leave_type", "from_date", "leave_approver"],
	)
	sent = 0
	for row in rows:
		doc = frappe._dict(doctype=LEAVE, name=row.name)
		when = frappe.format(row.from_date, "Date")
		approver = _active_users([row.leave_approver])
		if approver:
			_notify(
				approver,
				doc,
				subject=_("{0}'s leave starts {1} and is still waiting on you").format(row.employee_name, when),
				content=_("{0} asked for {1} starting {2}. Leave must be approved before it is taken, so it needs a decision now.").format(
					frappe.bold(row.employee_name), _(row.leave_type), when
				),
			)
			sent += 1
		applicant = _active_users([frappe.db.get_value("Employee", row.employee, "user_id")])
		if applicant:
			_notify(
				applicant,
				doc,
				subject=_("Your leave from {0} is not approved yet").format(when),
				content=_("Your {0} starting {1} has not been decided. Leave must be approved before you take it: speak to your approver.").format(
					_(row.leave_type), when
				),
			)
			sent += 1
	return sent
