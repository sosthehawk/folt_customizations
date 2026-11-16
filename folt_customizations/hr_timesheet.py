"""The monthly timesheet: one per employee, built from attendance, leave and the holiday list.

"Synchronise daily attendances and leave days with time sheets" is met by construction: nobody
types a timesheet. Its days ARE the month's Attendance rows (which HRMS writes for approved leave,
and hr_attendance writes for check-ins and HR's marks) laid over the employee's holiday list. A
day is one of:

  Worked / Worked from home   attendance Present / Work From Home     STANDARD_HOURS (or the hours
                                                                      the attendance row carries)
  Leave                       attendance On Leave, with its leave type 0 hours
  Half day                    attendance Half Day                     half the standard hours
  Absent                      attendance Absent                       0
  Holiday / Day off           on the holiday list, nothing marked     0
  Not marked                  a working day gone by with nothing      0 -- and it blocks sign-off
  Upcoming                    a working day still to come

The month is then signed in two steps, each recorded on a FoLT Timesheet (TS-<employee>-<month>):

  confirm_timesheet   the employee (or HR for them) confirms the month once its last working day
                      has come and no working day is left unmarked. The days are copied onto the
                      record and it goes to the employee's leave approver.
  decide_timesheet    that approver, any of the employee's approvers, or HR approves it -- the
                      days are copied again, as they stand at approval -- or returns it with a
                      reason. Nobody decides their own.

Until approval the page always shows the live days, so a correction by HR or a late leave approval
is simply there. After approval the record is the signed copy: if attendance or leave changes
underneath it, `timesheet` reports the days that no longer match (`changed_days`) and HR can return
it to be confirmed again.
"""

import frappe
from frappe import _
from frappe.utils import add_days, flt, get_last_day, getdate, now_datetime, nowdate

from folt_customizations import bff_support
from folt_customizations.hr_attendance import ATTENDANCE, is_hr
from folt_customizations.hr_portal import MONTH

TIMESHEET = "FoLT Timesheet"
STANDARD_HOURS = 8.0
EMPLOYEE_FIELDS = ["name", "employee_name", "department", "company", "leave_approver", "user_id", "date_of_joining", "relieving_date"]
KIND = {"Present": "Worked", "Work From Home": "Worked from home", "On Leave": "Leave", "Half Day": "Half day", "Absent": "Absent"}


# --- the days -----------------------------------------------------------------------------------


def _bounds(month):
	month = month or nowdate()[:7]
	if not MONTH.match(month):
		frappe.throw(_("The month must be given as YYYY-MM."))
	first = getdate(month + "-01")
	return month, first, getdate(get_last_day(first))


def month_days(employee, first, last) -> tuple[list[dict], dict]:
	"""The month, day by day, and its totals. `employee` is a row with EMPLOYEE_FIELDS."""
	today = getdate(nowdate())
	marks = {
		getdate(r.attendance_date): r
		for r in frappe.get_all(
			ATTENDANCE,
			filters={"employee": employee.name, "docstatus": 1, "attendance_date": ["between", [first, last]]},
			fields=["attendance_date", "status", "leave_type", "working_hours", "half_day_status"],
			limit_page_length=0,
		)
	}
	holidays = {getdate(h["date"]): h for h in bff_support.holidays_for(employee, first, last)["holidays"]}
	joined = getdate(employee.date_of_joining) if employee.date_of_joining else None
	left = getdate(employee.relieving_date) if employee.relieving_date else None

	days = []
	s = dict(working_days=0.0, worked=0.0, work_from_home=0.0, leave=0.0, absent=0.0, holidays=0, days_off=0, not_marked=0, hours=0.0)
	day = first
	while day <= last:
		mark, holiday = marks.get(day), holidays.get(day)
		kind, leave_type, hours, note = "", None, 0.0, None
		employed = not (joined and day < joined) and not (left and day > left)
		if mark:
			kind, leave_type = KIND[mark.status], mark.leave_type or None
			if mark.status in ("Present", "Work From Home"):
				hours = flt(mark.working_hours) or STANDARD_HOURS
				s["work_from_home" if mark.status == "Work From Home" else "worked"] += 1
			elif mark.status == "Half Day":
				# Half a day worked; the other half is leave when a leave type says so, else absence.
				hours = STANDARD_HOURS / 2
				s["worked"] += 0.5
				s["leave" if leave_type else "absent"] += 0.5
			elif mark.status == "On Leave":
				s["leave"] += 1
			else:
				s["absent"] += 1
			s["working_days"] += 1
		elif not employed:
			kind = "Not employed"
		elif holiday:
			kind = "Day off" if holiday["weekly_off"] else "Holiday"
			note = None if holiday["weekly_off"] else holiday["description"]
			s["days_off" if holiday["weekly_off"] else "holidays"] += 1
		else:
			s["working_days"] += 1
			if day <= today:
				kind = "Not marked"
				s["not_marked"] += 1
			else:
				kind = "Upcoming"
		s["hours"] += hours
		days.append({"date": str(day), "kind": kind, "leave_type": leave_type, "hours": hours, "note": note})
		day = add_days(day, 1)
	return days, s


def _last_working_day(days):
	for d in reversed(days):
		if d["kind"] not in ("Holiday", "Day off", "Not employed"):
			return getdate(d["date"])
	return None


def _blockers(days, summary) -> list[str]:
	"""Why this month cannot be confirmed or approved yet, in words; empty when it can."""
	out = []
	last = _last_working_day(days)
	if last and last > getdate(nowdate()):
		out.append(_("The month is not over: it can be confirmed from its last working day, {0}.").format(frappe.utils.formatdate(last, "d MMMM yyyy")))
	if summary["not_marked"]:
		out.append(
			_("{0} working day(s) have no attendance. Ask HR to mark them, or apply for leave if you were away.").format(
				summary["not_marked"]
			)
		)
	return out


# --- who may see and sign -----------------------------------------------------------------------


def _employee(name=None):
	"""The target employee row: `name`, or the session's own."""
	if name:
		return frappe.db.get_value("Employee", name, EMPLOYEE_FIELDS, as_dict=True)
	return frappe.db.get_value("Employee", {"user_id": frappe.session.user, "status": "Active"}, EMPLOYEE_FIELDS, as_dict=True)


def _approvers(employee) -> list[str]:
	"""The enabled users who approve for this employee, nearest first, never the employee."""
	candidates, _own = bff_support.approver_candidates(employee)
	unique = [u for u in dict.fromkeys(candidates) if u and u != employee.user_id]
	if not unique:
		return []
	enabled = set(frappe.get_all("User", filters={"name": ["in", unique], "enabled": 1}, pluck="name"))
	return [u for u in unique if u in enabled]


def _access(employee):
	"""(is_own, may_decide) for the session user, or a refusal when the timesheet is not theirs to see."""
	user = frappe.session.user
	own = bool(employee.user_id) and employee.user_id == user
	decides = not own and (is_hr() or user in _approvers(employee))
	if not own and not decides:
		frappe.throw(_("You can only see your own timesheet, or those you approve."), frappe.PermissionError)
	return own, decides


def _record(employee, month):
	name = frappe.db.get_value(TIMESHEET, {"employee": employee, "month": month}, "name")
	return frappe.get_doc(TIMESHEET, name) if name else None


def _full_name(user):
	return (frappe.db.get_value("User", user, "full_name", cache=True) or user) if user else None


def _record_view(doc):
	if not doc:
		return None
	return {
		"name": doc.name,
		"status": doc.status,
		"submitted_by": _full_name(doc.submitted_by),
		"submitted_on": str(doc.submitted_on) if doc.submitted_on else None,
		"approver": _full_name(doc.approver),
		"approved_by": _full_name(doc.approved_by),
		"approved_on": str(doc.approved_on) if doc.approved_on else None,
		"return_reason": doc.return_reason or None,
	}


def _summary_view(s):
	return {
		"working_days": s["working_days"],
		"worked": s["worked"],
		"work_from_home": s["work_from_home"],
		"leave": s["leave"],
		"absent": s["absent"],
		"holidays": s["holidays"],
		"days_off": s["days_off"],
		"not_marked": s["not_marked"],
		"hours": s["hours"],
	}


def _stamp(doc, employee, month, first, last, days, s):
	doc.update(
		{
			"employee": employee.name,
			"employee_name": employee.employee_name,
			"department": employee.department,
			"month": month,
			"from_date": first,
			"to_date": last,
			"working_days": s["working_days"],
			"days_worked": s["worked"] + s["work_from_home"],
			"days_leave": s["leave"],
			"days_absent": s["absent"],
			"days_holiday": s["holidays"],
			"hours_total": s["hours"],
		}
	)
	doc.set("days", [])
	for d in days:
		doc.append("days", {"date": d["date"], "kind": d["kind"], "leave_type": d["leave_type"], "hours": d["hours"]})


def _changed(doc, days) -> list[str]:
	"""The dates on which the signed copy no longer matches attendance."""
	signed = {str(r.date): (r.kind, r.leave_type or None, flt(r.hours)) for r in doc.days}
	return [d["date"] for d in days if signed.get(d["date"]) != (d["kind"], d["leave_type"], flt(d["hours"]))]


def _notify(users, doc, subject, content):
	from folt_customizations.leave_notifications import _notify as send
	from folt_customizations.notifications import _active_users

	recipients = _active_users(users)
	recipients.discard(frappe.session.user)
	if recipients:
		send(recipients, doc, subject=subject, content=content)


# --- the page -----------------------------------------------------------------------------------


@frappe.whitelist(methods=["GET"])
def timesheet(month: str | None = None, employee: str | None = None) -> dict:
	"""One employee's month: mine, or -- for HR and the employee's approvers -- `employee`'s."""
	month, first, last = _bounds(month)
	row = _employee(employee)
	if not row:
		if employee:
			frappe.throw(_("That employee does not exist."), frappe.DoesNotExistError)
		return {"employee": None}
	own, decides = _access(row)
	days, s = month_days(row, first, last)
	doc = _record(row.name, month)
	status = doc.status if doc else None
	blockers = _blockers(days, s)
	open_to_confirm = status in (None, "Returned")
	return {
		"employee": row.name,
		"employee_name": row.employee_name,
		"department": row.department,
		"month": month,
		"from_date": str(first),
		"to_date": str(last),
		"standard_hours": STANDARD_HOURS,
		"own": own,
		"days": days,
		"summary": _summary_view(s),
		"record": _record_view(doc),
		"blockers": blockers if open_to_confirm or status == "Submitted" else [],
		"can_confirm": open_to_confirm and not blockers and (own or is_hr()),
		"can_approve": decides and status == "Submitted" and not blockers,
		"can_return": decides and (status == "Submitted" or (status == "Approved" and is_hr())),
		"changed_days": _changed(doc, days) if status == "Approved" else [],
	}


@frappe.whitelist(methods=["POST"])
def confirm_timesheet(month: str, employee: str | None = None) -> dict:
	"""The employee confirms their month (or HR does for them); it goes to their approver."""
	month, first, last = _bounds(month)
	row = _employee(employee)
	if not row:
		frappe.throw(_("There is no employee record to keep a timesheet for."), frappe.DoesNotExistError)
	own = bool(row.user_id) and row.user_id == frappe.session.user
	if not own and not is_hr():
		frappe.throw(_("You confirm your own timesheet; HR can confirm one for somebody without a login."), frappe.PermissionError)
	doc = _record(row.name, month)
	if doc and doc.status in ("Submitted", "Approved"):
		frappe.throw(_("This timesheet is already {0}.").format(_(doc.status).lower()), title=_("Timesheet"))
	days, s = month_days(row, first, last)
	blockers = _blockers(days, s)
	if blockers:
		frappe.throw("<br>".join(blockers), title=_("Not ready to confirm"))

	approvers = _approvers(row)
	doc = doc or frappe.new_doc(TIMESHEET)
	_stamp(doc, row, month, first, last, days, s)
	doc.update(
		{
			"status": "Submitted",
			"submitted_by": frappe.session.user,
			"submitted_on": now_datetime(),
			"approver": approvers[0] if approvers else None,
			"approved_by": None,
			"approved_on": None,
			"return_reason": None,
		}
	)
	doc.flags.ignore_permissions = True
	doc.save()
	_notify(
		approvers[:1] or _hr_users(),
		doc,
		subject=_("{0}'s timesheet for {1} is ready to approve").format(row.employee_name, _month_label(first)),
		content=_("{0} confirmed {1} days worked and {2} on leave in {3}.").format(
			frappe.bold(row.employee_name), flt(doc.days_worked), flt(doc.days_leave), _month_label(first)
		),
	)
	return timesheet(month, row.name)


@frappe.whitelist(methods=["POST"])
def decide_timesheet(name: str, action: str, reason: str | None = None) -> dict:
	"""Approve a confirmed timesheet, or return it with a reason."""
	if action not in ("approve", "return"):
		frappe.throw(_("Approve the timesheet or return it."), title=_("Timesheet"))
	if not frappe.db.exists(TIMESHEET, name):
		frappe.throw(_("That timesheet does not exist."), frappe.DoesNotExistError)
	doc = frappe.get_doc(TIMESHEET, name)
	row = _employee(doc.employee)
	own, decides = _access(row)
	if own or not decides:
		frappe.throw(_("Nobody approves their own timesheet."), frappe.PermissionError)
	month, first, last = _bounds(doc.month)
	reason = (reason or "").strip()

	if action == "approve":
		if doc.status != "Submitted":
			frappe.throw(_("Only a confirmed timesheet can be approved; this one is {0}.").format(_(doc.status).lower()), title=_("Timesheet"))
		days, s = month_days(row, first, last)
		blockers = _blockers(days, s)
		if blockers:
			frappe.throw("<br>".join(blockers), title=_("Not ready to approve"))
		# The signed copy is the days as they stand now, not as they stood when it was confirmed.
		_stamp(doc, row, month, first, last, days, s)
		doc.update({"status": "Approved", "approved_by": frappe.session.user, "approved_on": now_datetime(), "return_reason": None})
		subject = _("Your timesheet for {0} was approved").format(_month_label(first))
		content = _("{0} approved your timesheet for {1}.").format(frappe.bold(_full_name(frappe.session.user)), _month_label(first))
	else:
		if doc.status == "Approved" and not is_hr():
			frappe.throw(_("Only HR can reopen an approved timesheet."), frappe.PermissionError)
		if doc.status not in ("Submitted", "Approved"):
			frappe.throw(_("This timesheet is not waiting for a decision."), title=_("Timesheet"))
		if not reason:
			frappe.throw(_("Say why it is being returned."), title=_("Timesheet"))
		doc.update({"status": "Returned", "return_reason": reason[:2000], "approved_by": None, "approved_on": None})
		subject = _("Your timesheet for {0} was returned").format(_month_label(first))
		content = _("{0} returned your timesheet for {1}: {2}").format(
			frappe.bold(_full_name(frappe.session.user)), _month_label(first), frappe.utils.escape_html(reason[:2000])
		)
	doc.flags.ignore_permissions = True
	doc.save()
	_notify([row.user_id], doc, subject=subject, content=content)
	return timesheet(month, row.name)


@frappe.whitelist(methods=["GET"])
def timesheets(month: str | None = None, department: str | None = None) -> dict:
	"""The month across the people the caller signs for: HR everyone, an approver their own."""
	month, first, last = _bounds(month)
	user, hr = frappe.session.user, is_hr()
	filters = {"status": "Active"}
	if department and hr:
		filters["department"] = department
	employees = frappe.get_all("Employee", filters=filters, fields=EMPLOYEE_FIELDS, order_by="employee_name asc", limit_page_length=0)
	records = {
		r.employee: r
		for r in frappe.get_all(TIMESHEET, filters={"month": month}, fields=["name", "employee", "status", "submitted_on", "approved_on"], limit_page_length=0)
	}
	rows = []
	for emp in employees:
		if emp.date_of_joining and getdate(emp.date_of_joining) > last:
			continue
		own = bool(emp.user_id) and emp.user_id == user
		mine_to_sign = not own and (hr or user in _approvers(emp))
		if not mine_to_sign:
			continue
		days, s = month_days(emp, first, last)
		record = records.get(emp.name)
		status = record.status if record else None
		rows.append(
			{
				"employee": emp.name,
				"employee_name": emp.employee_name,
				"department": emp.department,
				"name": record.name if record else None,
				"status": status,
				"summary": _summary_view(s),
				"can_approve": status == "Submitted" and not _blockers(days, s),
			}
		)
	return {"month": month, "from_date": str(first), "to_date": str(last), "department": filters.get("department"), "rows": rows}


def _hr_users():
	return frappe.get_all("Has Role", filters={"role": "HR Manager", "parenttype": "User"}, pluck="parent")


def _month_label(first):
	return frappe.utils.formatdate(first, "MMMM yyyy")
