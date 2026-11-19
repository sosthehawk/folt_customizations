"""End-to-end check of the HR policy on leave, attendance and timesheets.

Claims (leave_policy.py, hr_attendance.py, hr_timesheet.py):
- Notice: leave asked for at under seven days' notice is refused; marked as an emergency it still
  needs a reason; with one it is accepted and flagged. A request with enough notice is never an
  emergency, whatever was ticked. A request must name an approver.
- Overlaps: the applicant and the approver are both shown who else in the department is away on
  those days and how thin the department would be; nobody else can ask about another employee.
- Approval before leave: HR Settings make an approver mandatory and stop self-approval, and the
  morning reminder reaches the approver and the applicant of an undecided request about to start.
- Attendance: an employee marks today once, as Present or Work From Home, and nothing else; HR
  marks, corrects and clears any day up to today, but not a day approved leave has marked; the
  register lists everyone, a working day with nothing recorded as present by default.
- Timesheet: the month is the attendance, leave and holidays, day by day; a working day with
  nothing recorded is worked by default, so nothing blocks it; confirmed, it goes to the approver; approved, a later change to
  attendance shows as a changed day; only HR reopens an approved one, with a reason.
- Team calendar: planned activities are the requisitions that are approved or on their way, for
  staff only.

Everything runs in one transaction that is rolled back. Run with
    bench --site <site> execute folt_customizations.hr_policy_e2e.run
"""

import frappe
from frappe.utils import add_days, add_months, get_first_day, getdate, nowdate

from folt_customizations import hr_attendance, hr_timesheet, leave_policy

REQUESTER = "requester.test@folt.test"
HR = "hrmanager.test@folt.test"
LWP = "Leave Without Pay"
results = []


def check(label, condition, detail=""):
	results.append(bool(condition))
	print(("  ok    " if condition else "  FAIL  ") + label + (f"  [{detail}]" if detail and not condition else ""))


def expect_throw(label, fn, exc=Exception, saying=None):
	try:
		fn()
	except exc as e:
		check(label, saying is None or saying.lower() in str(e).lower(), str(e)[:200])
		return
	except Exception as e:
		check(label, False, f"{type(e).__name__}: {e}"[:200])
		return
	check(label, False, "nothing was refused")


def as_user(user, fn):
	previous = frappe.session.user
	frappe.set_user(user)
	try:
		return fn()
	finally:
		frappe.set_user(previous)
		frappe.local.message_log = []


def leave(employee, start, end, approver, **more):
	doc = frappe.get_doc(
		{"doctype": "Leave Application", "employee": employee, "leave_type": LWP, "from_date": start, "to_date": end,
		 "leave_approver": approver, "status": "Open", **more}
	)
	doc.insert()
	return doc


def run():
	frappe.set_user("Administrator")
	results.clear()
	try:
		_run()
	finally:
		frappe.db.rollback()
		frappe.set_user("Administrator")
	failed = results.count(False)
	print(f"\n{len(results) - failed}/{len(results)} checks passed")
	if failed:
		raise SystemExit(1)


def _run():
	today = getdate(nowdate())
	me = frappe.db.get_value("Employee", {"user_id": REQUESTER}, ["name", "department", "leave_approver"], as_dict=True)
	mates = frappe.get_all(
		"Employee", filters={"department": me.department, "status": "Active", "name": ["!=", me.name], "user_id": ["is", "not set"]},
		fields=["name", "employee_name", "leave_approver"], order_by="name asc", limit=2,
	)
	mate, other = mates[0], mates[1]
	approver = me.leave_approver
	# HRMS refuses unpaid leave in a period whose salary is already processed, and this site has
	# test payroll runs. Inside this transaction (rolled back) those slips are out of the way.
	frappe.db.delete("Salary Slip", {"employee": ["in", [me.name, mate.name, other.name]], "end_date": [">=", add_months(today, -2)]})
	# Earlier runs and the seeders may have left leave in the way of these dates.
	far, far_end = add_days(today, 40), add_days(today, 46)

	print("\n--- 1  seven days' notice, and the emergency exception ---")
	expect_throw("leave in three days is refused", lambda: as_user(REQUESTER, lambda: leave(me.name, add_days(today, 3), add_days(today, 3), approver)), saying="at least 7 days")
	expect_throw(
		"an emergency needs its reason",
		lambda: as_user(REQUESTER, lambda: leave(me.name, add_days(today, 3), add_days(today, 3), approver, folt_emergency=1)),
		saying="what the emergency is",
	)
	urgent = as_user(
		REQUESTER,
		lambda: leave(me.name, add_days(today, 1), add_days(today, 7), approver, folt_emergency=1, folt_emergency_reason="A death in the family."),
	)
	check("an emergency with a reason is accepted and flagged", urgent.folt_emergency == 1 and urgent.status == "Open")
	planned = as_user(REQUESTER, lambda: leave(me.name, far, far_end, approver, folt_emergency=1, folt_emergency_reason="Not really."))
	check("leave with enough notice is not an emergency, whatever was ticked", not planned.folt_emergency and not planned.folt_emergency_reason)
	expect_throw(
		"a request must name an approver",
		lambda: as_user(REQUESTER, lambda: leave(me.name, add_days(today, 60), add_days(today, 66), None)),
		saying="no leave approver",
	)
	check("deciding late is not refused for short notice", _saves(urgent))

	print("\n--- 2  overlapping leave in a department ---")
	as_user(HR, lambda: leave(mate.name, add_days(far, 1), add_days(far, 3), mate.leave_approver or approver))
	seen = as_user(REQUESTER, lambda: leave_policy.leave_check(str(far), str(far_end)))
	check("the applicant is shown the colleague who is away", [o["employee"] for o in seen["overlaps"]] == [mate.name], seen["overlaps"])
	check("with the department's size and its thinnest day", seen["headcount"] >= 3 and seen["peak_away"] == 2 and seen["peak_date"] == str(add_days(far, 1)), seen)
	check("and that this is not short notice", seen["short_notice"] is False and seen["notice_days"] == 7)
	check("a colleague's overlap carries no leave type or reason", set(seen["overlaps"][0]) == {"name", "employee", "employee_name", "from_date", "to_date", "status"})
	check("three days ahead is short notice", as_user(REQUESTER, lambda: leave_policy.leave_check(str(add_days(today, 3)), str(add_days(today, 3))))["short_notice"] is True)
	expect_throw("only HR checks for someone else", lambda: as_user(REQUESTER, lambda: leave_policy.leave_check(str(far), str(far_end), mate.name)), frappe.PermissionError)
	for_approver = as_user(approver, lambda: leave_policy.overlaps_for([planned.name, urgent.name]))
	check("the approver sees the same overlap on the request", [o["employee"] for o in for_approver[planned.name]["overlaps"]] == [mate.name])
	check("and that the emergency came at short notice", for_approver[urgent.name]["short_notice"] is True and for_approver[planned.name]["short_notice"] is False)
	expect_throw("an employee cannot ask about the queue", lambda: as_user(REQUESTER, lambda: leave_policy.overlaps_for([planned.name])), frappe.PermissionError)

	print("\n--- 3  approval before leave ---")
	leave_policy.apply_leave_policy()
	settings = frappe.get_single("HR Settings")
	check("an approver is mandatory and nobody approves their own", settings.leave_approver_mandatory_in_leave_application == 1 and settings.prevent_self_leave_approval == 1)
	check("the settings pass is idempotent", leave_policy.apply_leave_policy() == [])
	check("an undecided request starting tomorrow reminds the approver and the applicant", leave_policy.remind_undecided() >= 2)

	print("\n--- 4  daily attendance ---")
	frappe.db.delete("Attendance", {"employee": me.name, "attendance_date": today})
	urgent.reload()
	if hr_attendance._is_day_off(me.name, today):
		expect_throw("no check-in on a day off", lambda: as_user(REQUESTER, hr_attendance.check_in), saying="day off")
	else:
		marked = as_user(REQUESTER, lambda: hr_attendance.check_in("Work From Home"))
		check("an employee marks today", marked["status"] == "Work From Home" and marked["date"] == str(today), marked)
		expect_throw("and only once", lambda: as_user(REQUESTER, hr_attendance.check_in), saying="already marked")
	expect_throw("not as absent", lambda: as_user(REQUESTER, lambda: hr_attendance.check_in("Absent")), saying="Present or Work From Home")
	expect_throw("an employee cannot mark someone's day", lambda: as_user(REQUESTER, lambda: hr_attendance.mark(mate.name, str(today), "Present")), frappe.PermissionError)
	expect_throw("or read the register", lambda: as_user(REQUESTER, hr_attendance.register), frappe.PermissionError)

	workday = _past_workday(other, today)
	frappe.db.delete("Attendance", {"employee": other.name, "attendance_date": workday})
	check("HR marks a day gone by", as_user(HR, lambda: hr_attendance.mark(other.name, str(workday), "Absent"))["status"] == "Absent")
	check("corrects it", as_user(HR, lambda: hr_attendance.mark(other.name, str(workday), "Present"))["status"] == "Present")
	check("one mark a day survives a correction", frappe.db.count("Attendance", {"employee": other.name, "attendance_date": workday, "docstatus": 1}) == 1)
	register = as_user(HR, lambda: hr_attendance.register(str(workday)))
	row = next(r for r in register["rows"] if r["employee"] == other.name)
	check("the register shows it", row["status"] == "Present" and register["summary"]["present"] >= 1, row)
	check("and clears it", as_user(HR, lambda: hr_attendance.mark(other.name, str(workday), ""))["status"] is None and not hr_attendance._existing(other.name, workday))
	register = as_user(HR, lambda: hr_attendance.register(str(workday)))
	row = next(r for r in register["rows"] if r["employee"] == other.name)
	check("a working day with nothing recorded is present by default", row["status"] == "Present" and row["by_default"] and register["summary"]["unmarked"] == 0, row)
	check("and nothing is written for it", not hr_attendance._existing(other.name, workday))
	future = as_user(HR, lambda: hr_attendance.register(str(add_days(today, 1))))
	check("a day still to come is not defaulted", not any(r["by_default"] for r in future["rows"]), future["summary"])
	expect_throw("never a future day", lambda: as_user(HR, lambda: hr_attendance.mark(other.name, str(add_days(today, 1)), "Present")), saying="future")

	sick = as_user(HR, lambda: leave(other.name, workday, workday, other.leave_approver or approver, folt_emergency=1, folt_emergency_reason="Taken ill."))
	sick.status = "Approved"
	as_user(HR, sick.submit)
	check("approved leave writes the day's attendance", (hr_attendance._existing(other.name, workday) or {}).get("status") == "On Leave")
	expect_throw("which HR cannot overwrite", lambda: as_user(HR, lambda: hr_attendance.mark(other.name, str(workday), "Present")), saying="approved leave")

	print("\n--- 5  the monthly timesheet ---")
	month = str(add_months(get_first_day(today), -1))[:7]
	frappe.db.delete("FoLT Timesheet", {"employee": mate.name, "month": month})
	sheet = as_user(HR, lambda: hr_timesheet.timesheet(month, mate.name))
	unmarked = [d["date"] for d in sheet["days"] if d["note"] == hr_timesheet.PRESENT_BY_DEFAULT]
	by_default = [d for d in sheet["days"] if d["date"] in unmarked]
	check(
		"working days with nothing recorded are worked by default",
		unmarked and all(d["kind"] == "Worked" and d["hours"] == 8 for d in by_default) and sheet["summary"]["not_marked"] == 0,
		sheet["summary"],
	)
	check("so they do not block confirming the month", sheet["can_confirm"] and not sheet["blockers"], sheet["blockers"])
	before = sheet["summary"]
	as_user(HR, lambda: hr_attendance.mark(mate.name, unmarked[-1], "Half Day"))
	sheet = as_user(HR, lambda: hr_timesheet.timesheet(month, mate.name))
	s = sheet["summary"]
	check("an exception HR records replaces the default", s["not_marked"] == 0 and s["worked"] == before["worked"] - 0.5 and s["absent"] == before["absent"] + 0.5, s)
	check("hours follow the days", s["hours"] == before["hours"] - 4, s["hours"])
	check("holidays and days off are not working days", s["working_days"] + s["holidays"] + s["days_off"] == len(sheet["days"]) and s["days_off"] >= 4, s)
	expect_throw("a colleague cannot see it", lambda: as_user(REQUESTER, lambda: hr_timesheet.timesheet(month, mate.name)), frappe.PermissionError)
	expect_throw("or confirm it", lambda: as_user(REQUESTER, lambda: hr_timesheet.confirm_timesheet(month, mate.name)), frappe.PermissionError)
	confirmed = as_user(HR, lambda: hr_timesheet.confirm_timesheet(month, mate.name))
	sent_to = frappe.db.get_value("FoLT Timesheet", confirmed["record"]["name"], "approver")
	check("HR confirms it for someone without a login, and it goes to their approver", confirmed["record"]["status"] == "Submitted" and sent_to == (mate.leave_approver or approver), sent_to)
	expect_throw("not twice", lambda: as_user(HR, lambda: hr_timesheet.confirm_timesheet(month, mate.name)), saying="already")
	queue = as_user(sent_to, lambda: hr_timesheet.timesheets(month))
	mine = next((r for r in queue["rows"] if r["employee"] == mate.name), None)
	check("the approver finds it in their list, ready", mine and mine["status"] == "Submitted" and mine["can_approve"], mine)
	check("an approver's list is their own people", all(r["department"] == me.department for r in queue["rows"]), [r["department"] for r in queue["rows"]])
	name = confirmed["record"]["name"]
	expect_throw("an employee cannot decide it", lambda: as_user(REQUESTER, lambda: hr_timesheet.decide_timesheet(name, "approve")), frappe.PermissionError)
	approved = as_user(sent_to, lambda: hr_timesheet.decide_timesheet(name, "approve"))
	check("the approver approves it", approved["record"]["status"] == "Approved" and approved["changed_days"] == [] and not approved["can_approve"])
	check("the signed copy holds every day", frappe.db.count("FoLT Timesheet Day", {"parent": name}) == len(sheet["days"]))
	as_user(HR, lambda: hr_attendance.mark(mate.name, unmarked[0], "Absent"))
	drifted = as_user(HR, lambda: hr_timesheet.timesheet(month, mate.name))
	check("a later change to attendance shows as a changed day", drifted["changed_days"] == [unmarked[0]], drifted["changed_days"])
	expect_throw("only HR reopens an approved timesheet", lambda: as_user(sent_to, lambda: hr_timesheet.decide_timesheet(name, "return", "Changed.")), frappe.PermissionError)
	expect_throw("and with a reason", lambda: as_user(HR, lambda: hr_timesheet.decide_timesheet(name, "return")), saying="why")
	returned = as_user(HR, lambda: hr_timesheet.decide_timesheet(name, "return", "A day was corrected."))
	check("returned, it can be confirmed again", returned["record"]["status"] == "Returned" and returned["can_confirm"] and returned["record"]["return_reason"] == "A day was corrected.")
	own = as_user(REQUESTER, hr_timesheet.timesheet)
	check("my own current month is mine and not yet confirmable", own["own"] and own["employee"] == me.name and (not own["can_confirm"] or not own["blockers"]))

	print("\n--- 6  activities on the team calendar ---")
	first, last = get_first_day(today), frappe.utils.get_last_day(today)
	shown = as_user(REQUESTER, lambda: hr_attendance.planned_activities(str(first), str(last)))["activities"]
	expected = frappe.db.sql(
		"""select count(*) from `tabActivity Requisition` where docstatus < 2 and workflow_state not in ('Draft', 'Rejected')
		and activity_date <= %s and coalesce(activity_end_date, activity_date) >= %s""",
		(last, first),
	)[0][0]
	check("every activity approved or on its way this month is shown", len(shown) == expected, f"{len(shown)} vs {expected}")
	check("as a title, dates and place only", all(set(a) == {"name", "title", "from_date", "to_date", "place", "programme", "led_by", "department", "approved"} for a in shown))
	portal = frappe.db.get_value("User", {"user_type": "Website User", "enabled": 1, "name": ["not in", ["Guest"]]}, "name")
	if portal:
		expect_throw("a supplier is refused", lambda: as_user(portal, lambda: hr_attendance.planned_activities(str(first), str(last))), frappe.PermissionError)


def _saves(doc):
	doc.reload()
	doc.description = "Same request, saved again."
	doc.save()
	return True


def _past_workday(employee, today):
	day = add_days(today, -1)
	while hr_attendance._is_day_off(employee.name, day) or frappe.db.exists(
		"Leave Application", {"employee": employee.name, "from_date": ["<=", day], "to_date": [">=", day], "docstatus": ["<", 2]}
	):
		day = add_days(day, -1)
	return day
