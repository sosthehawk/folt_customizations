"""Daily attendance from the app: staff check themselves in, HR marks and corrects.

HRMS's Attendance is HR's to write -- the Employee role can only read it -- and nothing in the
FoLT app marked it at all, so the only attendance on the site was the "On Leave" days HRMS writes
when leave is approved. These are the missing halves:

  check_in(status)              The session employee marks TODAY, as Present or Work From Home.
                                Only today, only their own, only once, and not on a day their
                                holiday list gives them off or that approved leave already covers.
                                Written with the permission check waived, because the rule above
                                IS the check: the Employee role has no Attendance create.
  mark(employee, date, status)  HR marks or corrects anyone's day, up to today: Present, Absent,
                                Work From Home, Half Day, or "" to clear it. A day that approved
                                leave marked is the leave's to change (cancel the leave), not HR's.
  register(date, department)    HR's daily register: every active employee with that day's mark.
                                PRESENT UNLESS ASSIGNED OTHERWISE: a working day (today or gone by)
                                with nothing recorded reads as Present, `by_default`. Nothing is
                                written for it -- a stored Present row would make HRMS refuse leave
                                applied afterwards for that day (sick leave), and payroll here is
                                leave-based, so an unrecorded day is already a paid day. HR records
                                the exceptions; clearing a mark returns the day to the default.
  planned_activities(from, to)  The team calendar's activities: every Activity Requisition that is
                                approved or on its way to approval and falls in the range, as a
                                title, its dates, the place and who leads it -- never the budget.

Leave and attendance cannot disagree: HRMS writes the On Leave / Half Day attendance when leave is
approved and removes it when the leave is cancelled, and refuses leave over a day already marked
Present. The monthly timesheet (hr_timesheet.py) is built from these same rows.
"""

import frappe
from frappe import _
from frappe.utils import getdate, nowdate, strip_html

HR_ROLES = {"HR User", "HR Manager"}
SELF_STATUSES = ("Present", "Work From Home")
HR_STATUSES = ("Present", "Absent", "Work From Home", "Half Day")
ATTENDANCE = "Attendance"
ACTIVITY = "Activity Requisition"


def is_hr() -> bool:
	return frappe.session.user == "Administrator" or bool(HR_ROLES.intersection(frappe.get_roles()))


def require_hr():
	if not is_hr():
		frappe.throw(_("Only HR can do this."), frappe.PermissionError)


def own_employee(fields=("name", "employee_name", "department", "company")):
	return frappe.db.get_value("Employee", {"user_id": frappe.session.user, "status": "Active"}, list(fields), as_dict=True)


def _existing(employee, date):
	return frappe.db.get_value(
		ATTENDANCE,
		{"employee": employee, "attendance_date": date, "docstatus": ["!=", 2]},
		["name", "status", "leave_application", "leave_type", "docstatus"],
		as_dict=True,
	)


def _is_day_off(employee, date) -> bool:
	"""On the holiday list HRMS applies to this employee that day (weekly offs included)."""
	from hrms.utils.holiday_list import get_holiday_list_for_employee

	holiday_list = get_holiday_list_for_employee(employee, raise_exception=False, as_on=date)
	return bool(
		holiday_list and frappe.db.exists("Holiday", {"parent": holiday_list, "parenttype": "Holiday List", "holiday_date": date})
	)


def _write(employee, date, status):
	doc = frappe.new_doc(ATTENDANCE)
	doc.update({"employee": employee, "attendance_date": date, "status": status})
	doc.flags.ignore_permissions = True
	doc.insert()
	doc.submit()
	return doc


def _day(doc) -> dict:
	return {
		"employee": doc.employee,
		"date": str(doc.attendance_date),
		"status": doc.status,
		"leave_type": doc.leave_type or None,
	}


# --- the employee's own day ---------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def check_in(status: str = "Present") -> dict:
	"""Mark today for the session employee."""
	if status not in SELF_STATUSES:
		frappe.throw(_("You can mark yourself Present or Work From Home."), title=_("Attendance"))
	employee = own_employee()
	if not employee:
		frappe.throw(_("Your login is not linked to an employee record, so there is no attendance to mark."), frappe.DoesNotExistError)
	today = getdate(nowdate())
	existing = _existing(employee.name, today)
	if existing:
		frappe.throw(_("Today is already marked for you as {0}.").format(frappe.bold(_(existing.status))), title=_("Already marked"))
	if _is_day_off(employee.name, today):
		frappe.throw(_("Today is a holiday or a day off on your holiday list. If you worked, ask HR to record it."), title=_("Day off"))
	doc = _write(employee.name, today, status)
	# HRMS turns a mark on a day of approved leave into On Leave; say so rather than "Present".
	return _day(doc)


# --- HR's register ------------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def mark(employee: str, date: str, status: str = "") -> dict:
	"""HR marks, corrects or clears one employee's day."""
	require_hr()
	status = (status or "").strip()
	if status and status not in HR_STATUSES:
		frappe.throw(_("{0} is not an attendance status HR can set.").format(frappe.bold(status)), title=_("Attendance"))
	day = getdate(date) if date else None
	if not day:
		frappe.throw(_("Choose the day."), title=_("Attendance"))
	if day > getdate(nowdate()):
		frappe.throw(_("Attendance is marked for today or a day gone by, not for the future."), title=_("Attendance"))
	if not frappe.db.exists("Employee", {"name": employee, "status": "Active"}):
		frappe.throw(_("That employee does not exist or is not active."), frappe.DoesNotExistError)

	existing = _existing(employee, day)
	if existing and existing.leave_application:
		frappe.throw(
			_("{0} is on approved leave ({1}) that day. Cancel the leave to change the day.").format(
				frappe.bold(frappe.db.get_value("Employee", employee, "employee_name")), existing.leave_application
			),
			title=_("On leave"),
		)
	if existing and existing.status == status:
		return {"employee": employee, "date": str(day), "status": status, "leave_type": existing.leave_type or None}
	if existing:
		old = frappe.get_doc(ATTENDANCE, existing.name)
		old.flags.ignore_permissions = True
		if old.docstatus == 1:
			old.cancel()
		else:
			old.delete()
	if not status:
		return {"employee": employee, "date": str(day), "status": None, "leave_type": None}
	return _day(_write(employee, day, status))


@frappe.whitelist(methods=["GET"])
def register(date: str | None = None, department: str | None = None) -> dict:
	"""Every active employee and their mark for one day."""
	require_hr()
	from hrms.utils.holiday_list import get_holiday_list_for_employee

	day = getdate(date) if date else getdate(nowdate())
	filters = {"status": "Active"}
	if department:
		filters["department"] = department
	employees = frappe.get_all(
		"Employee",
		filters=filters,
		fields=["name", "employee_name", "department", "date_of_joining"],
		order_by="employee_name asc",
		limit_page_length=0,
	)
	marks = {
		row.employee: row
		for row in frappe.get_all(
			ATTENDANCE,
			filters={"attendance_date": day, "docstatus": 1, "employee": ["in", [e.name for e in employees] or [""]]},
			fields=["employee", "status", "leave_type", "leave_application"],
			limit_page_length=0,
		)
	}
	off_by_list: dict[str, bool] = {}
	future = day > getdate(nowdate())
	rows, counts = [], {"present": 0, "work_from_home": 0, "absent": 0, "on_leave": 0, "half_day": 0, "day_off": 0, "unmarked": 0, "by_default": 0}
	key = {"Present": "present", "Work From Home": "work_from_home", "Absent": "absent", "On Leave": "on_leave", "Half Day": "half_day"}
	for emp in employees:
		if emp.date_of_joining and getdate(emp.date_of_joining) > day:
			continue
		mark_row = marks.get(emp.name)
		holiday_list = get_holiday_list_for_employee(emp.name, raise_exception=False, as_on=day)
		if holiday_list not in off_by_list:
			off_by_list[holiday_list] = bool(
				holiday_list and frappe.db.exists("Holiday", {"parent": holiday_list, "parenttype": "Holiday List", "holiday_date": day})
			)
		day_off = off_by_list[holiday_list]
		by_default = not mark_row and not day_off and not future
		if mark_row:
			counts[key[mark_row.status]] += 1
		elif day_off:
			counts["day_off"] += 1
		elif by_default:
			counts["present"] += 1
			counts["by_default"] += 1
		else:
			counts["unmarked"] += 1
		rows.append(
			{
				"employee": emp.name,
				"employee_name": emp.employee_name,
				"department": emp.department,
				"status": mark_row.status if mark_row else ("Present" if by_default else None),
				"leave_type": (mark_row.leave_type or None) if mark_row else None,
				"from_leave": bool(mark_row and mark_row.leave_application),
				"day_off": day_off,
				"by_default": by_default,
			}
		)
	return {"date": str(day), "today": nowdate(), "department": department or None, "rows": rows, "summary": counts}


# --- the team calendar's activities -------------------------------------------------------------


@frappe.whitelist(methods=["GET"])
def planned_activities(from_date: str, to_date: str) -> dict:
	"""Activities planned between two dates, for the team calendar.

	Every member of staff sees these whether or not they could open the requisition: when and
	where the organisation is busy is what a team calendar is for. So the rows are read without a
	permission check and carry only what a calendar shows. Portal users (suppliers) are refused."""
	if frappe.session.user == "Guest" or frappe.db.get_value("User", frappe.session.user, "user_type") != "System User":
		frappe.throw(_("The team calendar is for staff."), frappe.PermissionError)
	start, end = getdate(from_date), getdate(to_date)
	if not start or not end or end < start or (end - start).days > 366:
		frappe.throw(_("The range must run forwards and span at most a year."), title=_("Invalid range"))
	rows = frappe.db.sql(
		"""
		select a.name, a.activity_program, a.activity_date, a.activity_end_date, a.venue, a.location,
		       a.workflow_state, a.program, e.employee_name, e.department
		from `tabActivity Requisition` a
		left join `tabEmployee` e on e.name = a.requested_by
		where a.docstatus < 2
		  and a.workflow_state not in ('Draft', 'Rejected')
		  and a.activity_date <= %(end)s
		  and coalesce(a.activity_end_date, a.activity_date) >= %(start)s
		order by a.activity_date asc, a.name asc
		limit 500
		""",
		{"start": start, "end": end},
		as_dict=True,
	)
	return {
		"activities": [
			{
				"name": r.name,
				"title": strip_html(r.activity_program or "").strip() or r.name,
				"from_date": str(r.activity_date),
				"to_date": str(r.activity_end_date or r.activity_date),
				"place": r.venue or r.location or None,
				"programme": r.program or None,
				"led_by": r.employee_name or None,
				"department": r.department or None,
				"approved": r.workflow_state == "Approved",
			}
			for r in rows
		]
	}
