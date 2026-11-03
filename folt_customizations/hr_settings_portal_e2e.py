"""End-to-end check of hr_settings_portal, the server half of the FoLT app's HR > Settings (ADR-009).

Claims:
- Who gets in: HR User, HR Manager and System Manager reach HR settings; anybody else is refused;
  the doctype allow-list holds.
- HR User reads and never writes -- refused by the edge AND by Frappe itself (permissions.HR_READ_ONLY,
  applied by permissions.apply_read_only), and the read-only pass is idempotent and respects a
  permission an administrator gave back on purpose (the overrides store).
- HR Manager keeps the organisation lists and the single settings records through the edge: create,
  read, update with the version check, delete.
- Holidays (phase 2): ERPNext's own fill helpers run on an unsaved list and write nothing; a list is
  made and assigned to the company from a later date; the context names the list in force, the days
  it has left and the next assignment.
- Leave (phase 3): leave types, periods and policies kept by HR Manager; the Leave Control Panel's
  candidates (HRMS's own query) and bulk allocation by policy and by leave type, one employee's
  refusal not stopping the rest.
- Pay set-up (phase 4): FoLT's statutory components and structure are kept with the app (read,
  never written); HR Manager keeps the rest; HRMS's slip preview matches the slip a run made and
  saves nothing.

Runs in one transaction and rolls it back; the one stored default it touches (the overrides store)
is put back by hand and the cache cleared, because a rollback does not reach Redis. Run with
    bench --site <site> execute folt_customizations.hr_settings_portal_e2e.run
"""

import json

import frappe
from frappe.permissions import update_permission_property

from folt_customizations import hr_settings_portal as hs
from folt_customizations import permissions

HR_USER = "hruser.test@folt.test"
HR_MANAGER = "hrmanager.test@folt.test"
EMPLOYEE = "requester.test@folt.test"
FINANCE = "finofficer.test@folt.test"
BRANCH = "E2E HR Settings Branch"
COMPANY = "FRIENDS of LAKE TURKANA"
HOLIDAYS = "E2E HR Settings Holidays"

PASS, FAIL = [], []


def check(label, condition, detail=""):
	(PASS if condition else FAIL).append(label)
	print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  — ' + str(detail) if detail else ''}")


def expect_throw(label, fn, exc=Exception):
	try:
		fn()
	except exc as e:
		check(label, True, frappe.utils.strip_html(str(e)).strip()[:90].replace("\n", " ") or type(e).__name__)
		frappe.local.message_log = []
		return
	except Exception as e:  # noqa: BLE001
		check(label, False, f"wrong error type: {type(e).__name__}: {e}")
		frappe.local.message_log = []
		return
	check(label, False, "no error raised")


def as_user(user, fn):
	previous = frappe.session.user
	frappe.set_user(user)
	try:
		return fn()
	finally:
		frappe.set_user(previous)


def who_gets_in():
	print("Who gets in")
	for user in (EMPLOYEE, FINANCE):
		expect_throw(f"{user} is refused HR settings", lambda u=user: as_user(u, hs.context), frappe.PermissionError)
	ctx = as_user(HR_USER, hs.context)
	check("an HR User reads the context, as a reader", ctx["roles"]["editor"] is False and ctx["capabilities"]["Department"]["read"])
	check("an HR User is offered no write anywhere", not any(c["write"] or c["create"] or c["delete"] for c in ctx["capabilities"].values()))
	ctx = as_user(HR_MANAGER, hs.context)
	check("an HR Manager is an editor", ctx["roles"]["editor"] and ctx["capabilities"]["Branch"]["create"], ctx["counts"].get("Department"))
	expect_throw("a doctype outside the allow-list is refused", lambda: as_user(HR_MANAGER, lambda: hs.records("Salary Slip")), frappe.PermissionError)
	expect_throw("a meta outside the allow-list is refused", lambda: as_user(HR_MANAGER, lambda: hs.meta("User")), frappe.PermissionError)


def hr_user_reads_only():
	print("HR User reads, never writes")
	rows = as_user(HR_USER, lambda: hs.records("Department"))
	check("an HR User lists departments", rows["total"] > 0, rows["total"])
	single = as_user(HR_USER, lambda: hs.record("Payroll Settings"))
	check("an HR User reads Payroll Settings, offered no write", single["permissions"]["read"] and not single["permissions"]["write"])
	expect_throw("the edge refuses an HR User's save", lambda: as_user(HR_USER, lambda: hs.save("Branch", json.dumps({"branch": BRANCH}))), frappe.PermissionError)
	expect_throw(
		"Frappe itself refuses an HR User's insert (HR_READ_ONLY)",
		lambda: as_user(HR_USER, lambda: frappe.get_doc({"doctype": "Branch", "branch": BRANCH}).insert()),
		frappe.PermissionError,
	)
	for doctype in ("Leave Type", "Salary Structure", "Holiday List", "Salary Structure Assignment"):
		check(f"Frappe: HR User has no write on {doctype}", not as_user(HR_USER, lambda d=doctype: frappe.has_permission(d, "write")))
		check(f"Frappe: HR User reads {doctype}", as_user(HR_USER, lambda d=doctype: frappe.has_permission(d, "read")))


def read_only_pass():
	print("The read-only pass")
	permissions.apply_read_only()
	check("a second pass changes nothing", not permissions._has_perm("Branch", "HR User", "write"))
	before = frappe.db.get_global(permissions.OVERRIDES_KEY)
	try:
		# An administrator gives HR User write on Branch back, on purpose (Settings > Permissions).
		permissions.record_override("Branch", "HR User", 0, "write", 1)
		update_permission_property("Branch", "HR User", 0, "write", 1)
		frappe.clear_cache(doctype="Branch")
		permissions.apply_read_only()
		check("a write given back on purpose is kept", permissions._has_perm("Branch", "HR User", "write"))
	finally:
		frappe.db.set_global(permissions.OVERRIDES_KEY, before)
		update_permission_property("Branch", "HR User", 0, "write", 0)
		frappe.clear_cache(doctype="Branch")
	permissions.apply_read_only()
	check("without the override the write is gone again", not permissions._has_perm("Branch", "HR User", "write"))


def hr_manager_keeps_settings():
	print("HR Manager keeps the settings")

	def flow():
		made = hs.save("Branch", json.dumps({"branch": BRANCH}))
		check("an HR Manager adds a branch", made["name"] == BRANCH)
		rows = hs.records("Branch", q="E2E HR Settings")
		check("the branch is listed", any(r["name"] == BRANCH for r in rows["rows"]))
		rec = hs.record("Branch", BRANCH)
		check("the branch opens with write", rec["permissions"]["write"] and rec["doc"]["branch"] == BRANCH)
		grade = hs.save("Employee Grade", json.dumps({"__newname": "E2E HR Settings Grade", "currency": "KES", "default_base_pay": 50000}))
		check("an HR Manager adds a grade, named by prompt", grade["name"] == "E2E HR Settings Grade")
		expect_throw("a prompt-named record needs its name", lambda: hs.save("Employee Grade", json.dumps({"currency": "KES"})))
		meta = hs.meta("Employee Grade")
		check("a grade's form names its default structure and base pay", {"default_salary_structure", "default_base_pay"} <= {f["fieldname"] for f in meta["fields"]})
		single = hs.record("HR Settings")
		before = single["doc"].get("retirement_age")
		hs.save("HR Settings", json.dumps({"retirement_age": 61, "modified": single["doc"]["modified"]}))
		check("an HR Manager changes HR Settings", str(frappe.db.get_single_value("HR Settings", "retirement_age")) == "61", before)
		hs.act("Branch", BRANCH, "delete")
		check("an HR Manager deletes the branch", not frappe.db.exists("Branch", BRANCH))

	as_user(HR_MANAGER, flow)


def holidays():
	print("Holidays")
	from frappe.utils import add_days, add_years, getdate

	current = as_user(HR_MANAGER, hs.context)["holidays"]
	mine = next((h for h in current if h["company"] == COMPANY), None)
	check("the context names the company's list in force", bool(mine and mine["holiday_list"]), mine and f"{mine['holiday_list']}, {mine['days_left']} days left")
	start = str(add_days(getdate(mine["to_date"]), 1)) if mine and mine["to_date"] else "2030-07-01"
	end = str(add_days(add_years(getdate(start), 1), -1))
	draft = {"holiday_list_name": HOLIDAYS, "from_date": start, "to_date": end, "weekly_off": "Sunday", "country": "KE"}
	expect_throw("an HR User cannot fill a list", lambda: as_user(HR_USER, lambda: hs.fill_holidays(json.dumps(draft), "weekly_off")), frappe.PermissionError)

	def flow():
		before = frappe.db.count("Holiday List")
		local = hs.fill_holidays(json.dumps(draft), "local")
		check("public holidays come from ERPNext's helper", local["added"] >= 10 and all(not r["weekly_off"] for r in local["holidays"]), local["added"])
		both = hs.fill_holidays(json.dumps({**draft, "holidays": local["holidays"]}), "weekly_off")
		dates = [r["holiday_date"] for r in both["holidays"]]
		sundays = {str(add_days(getdate(start), i)) for i in range((getdate(end) - getdate(start)).days + 1) if add_days(getdate(start), i).weekday() == 6}
		already = sundays & {r["holiday_date"] for r in local["holidays"]}
		# Every Sunday once: those already in the list as a public holiday are kept, not doubled.
		check("weekly offs are added, nothing doubled, sorted", both["added"] == len(sundays - already) and dates == sorted(dates) and len(dates) == len(set(dates)),
		      f"{both['added']} added = {len(sundays)} Sundays - {len(already)} already public holidays")
		check("filling saves nothing", frappe.db.count("Holiday List") == before)
		cleared = hs.fill_holidays(json.dumps({**draft, "holidays": both["holidays"]}), "clear")
		check("clear empties the table", cleared["holidays"] == [])
		expect_throw("a fill needs the list's dates", lambda: hs.fill_holidays(json.dumps({"weekly_off": "Sunday"}), "weekly_off"))
		made = hs.save("Holiday List", json.dumps({**draft, "holidays": both["holidays"]}))
		check("the filled list saves", frappe.db.get_value("Holiday List", made["name"], "total_holidays") == len(both["holidays"]), made["name"])
		assigned = hs.save("Holiday List Assignment", json.dumps({"applicable_for": "Company", "assigned_to": COMPANY, "holiday_list": made["name"], "from_date": start}))
		hs.act("Holiday List Assignment", assigned["name"], "submit")
		check("the list is assigned to the company and submitted", frappe.db.get_value("Holiday List Assignment", assigned["name"], "docstatus") == 1)
		after = next(h for h in hs.context()["holidays"] if h["company"] == COMPANY)
		check("the context shows it as the next list", (after.get("next") or {}).get("holiday_list") == made["name"], after.get("next"))
		expect_throw(
			"a second assignment from the same date is refused (HRMS)",
			lambda: hs.act("Holiday List Assignment", hs.save("Holiday List Assignment", json.dumps({"applicable_for": "Company", "assigned_to": COMPANY, "holiday_list": made["name"], "from_date": start}))["name"], "submit"),
		)

	as_user(HR_MANAGER, flow)


def leave():
	print("Leave")
	ctx = as_user(HR_MANAGER, hs.context)
	spec_now = {"dates_based_on": "Custom Range", "from_date": "2026-07-01", "to_date": "2027-06-30", "allocate_based_on_leave_policy": 0,
	            "leave_type": "Annual Leave", "no_of_days": 21, "company": COMPANY}
	expect_throw("an HR User cannot look up candidates", lambda: as_user(HR_USER, lambda: hs.leave_candidates(json.dumps(spec_now))), frappe.PermissionError)
	expect_throw("an HR User cannot allocate", lambda: as_user(HR_USER, lambda: hs.allocate_leave(json.dumps(spec_now), json.dumps(["HR-EMP-00001"]))), frappe.PermissionError)
	check("the context reports the leave year per company", any(p["company"] == COMPANY for p in ctx["leave_periods"]), ctx["leave_periods"])

	def flow():
		lt = hs.save("Leave Type", json.dumps({"leave_type_name": "E2E HR Settings Leave", "max_leaves_allowed": 5}))
		check("an HR Manager adds a leave type", frappe.db.exists("Leave Type", lt["name"]))
		period = hs.save("Leave Period", json.dumps({"from_date": "2026-07-01", "to_date": "2027-06-30", "company": COMPANY, "is_active": 1}))
		now = next(p for p in hs.context()["leave_periods"] if p["company"] == COMPANY)
		check("the context names the active leave period covering today", now["leave_period"] == period["name"], now)
		policy = hs.save("Leave Policy", json.dumps({"title": "E2E HR Settings Policy", "leave_policy_details": [
			{"leave_type": "Annual Leave", "annual_allocation": 21}, {"leave_type": "Sick Leave", "annual_allocation": 7}]}))
		hs.act("Leave Policy", policy["name"], "submit")
		check("the policy is submitted", frappe.db.get_value("Leave Policy", policy["name"], "docstatus") == 1)
		check("nobody is a candidate where everyone has an allocation", hs.leave_candidates(json.dumps(spec_now)) == [])
		spec = {"dates_based_on": "Custom Range", "from_date": "2027-07-01", "to_date": "2028-06-30", "allocate_based_on_leave_policy": 1,
		        "leave_policy": policy["name"], "company": COMPANY}
		people = hs.leave_candidates(json.dumps(spec))
		active = frappe.db.count("Employee", {"company": COMPANY, "status": "Active"})
		check("every active employee is a candidate for the next year", len(people) == active, f"{len(people)} of {active}")
		pick = [p["employee"] for p in people[:2]]
		done = hs.allocate_leave(json.dumps(spec), json.dumps(pick))
		check("allocating by policy makes a submitted assignment each", len(done["done"]) == 2 and not done["failed"]
		      and all(frappe.db.get_value("Leave Policy Assignment", d["name"], "docstatus") == 1 for d in done["done"]))
		made = frappe.get_all("Leave Allocation", filters={"employee": pick[0], "from_date": "2027-07-01", "docstatus": 1}, fields=["leave_type", "new_leaves_allocated"])
		check("the assignment made the allocations (Annual 21, Sick 7)", sorted((a.leave_type, a.new_leaves_allocated) for a in made) == [("Annual Leave", 21.0), ("Sick Leave", 7.0)], made)
		check("they are no longer candidates", len(hs.leave_candidates(json.dumps(spec))) == active - 2)
		again = hs.allocate_leave(json.dumps(spec), json.dumps([pick[0], people[2]["employee"]]))
		check("a duplicate is refused by HRMS and the other still allocated", len(again["done"]) == 1 and len(again["failed"]) == 1, again["failed"])
		by_type = {"dates_based_on": "Custom Range", "from_date": "2027-07-01", "to_date": "2028-06-30", "allocate_based_on_leave_policy": 0,
		           "leave_type": lt["name"], "no_of_days": 3, "company": COMPANY}
		typed = hs.allocate_leave(json.dumps(by_type), json.dumps([pick[0]]))
		check("allocating one leave type makes a submitted allocation", len(typed["done"]) == 1 and typed["done"][0]["doctype"] == "Leave Allocation"
		      and frappe.db.get_value("Leave Allocation", typed["done"][0]["name"], "new_leaves_allocated") == 3)
		expect_throw("an unknown allocation field is refused", lambda: hs.leave_candidates(json.dumps({**spec, "naming_series": "X"})))
		expect_throw("a policy allocation needs its policy (HRMS)", lambda: hs.allocate_leave(json.dumps({**spec, "leave_policy": None}), json.dumps(pick)))

	as_user(HR_MANAGER, flow)


def pay_setup():
	print("Pay set-up")
	from frappe.utils import flt

	kept = hs.kept_with_app()
	check("the statutory components and the structure are kept with the app", "PAYE" in kept["Salary Component"] and "FoLT Kenya Payroll" in kept["Salary Structure"])

	def flow():
		check("a kept component opens without write, even for an HR Manager", not hs.record("Salary Component", "PAYE")["permissions"]["write"])
		expect_throw("a kept component cannot be saved", lambda: hs.save("Salary Component", json.dumps({"description": "x"}), "PAYE"), frappe.PermissionError)
		expect_throw("the kept structure cannot be cancelled", lambda: hs.act("Salary Structure", "FoLT Kenya Payroll", "cancel"), frappe.PermissionError)
		made = hs.save("Salary Component", json.dumps({"salary_component": "E2E HR Settings Allowance", "salary_component_abbr": "E2EA", "type": "Earning"}))
		check("an HR Manager adds a component of their own", hs.record("Salary Component", made["name"])["permissions"]["write"], made["name"])
		period = hs.save("Payroll Period", json.dumps({"__newname": "E2E HR Settings Tax Year", "company": COMPANY, "start_date": "2027-07-01", "end_date": "2028-06-30"}))
		check("an HR Manager adds a payroll period", frappe.db.exists("Payroll Period", period["name"]))
		shift = hs.save("Shift Type", json.dumps({"__newname": "E2E HR Settings Day Shift", "start_time": "08:00:00", "end_time": "17:00:00"}))
		check("an HR Manager adds a shift type", frappe.db.exists("Shift Type", shift["name"]))
		emp, slip = frappe.db.get_value("Salary Slip", {"docstatus": 1}, ["employee", "name"], order_by="start_date desc") or (None, None)
		real = frappe.get_doc("Salary Slip", slip) if slip else None
		before = frappe.db.count("Salary Slip")
		p = hs.preview_salary_slip("FoLT Kenya Payroll", emp, str(real.end_date)) if real else None
		check("the preview pays what the run's slip paid", bool(p) and flt(p["net_pay"]) == flt(real.net_pay) and flt(p["gross_pay"]) == flt(real.gross_pay),
		      p and f"{p['employee_name']}: gross {p['gross_pay']}, net {p['net_pay']} (slip {real.net_pay})")
		check("the preview leaves out statistical lines", p and all(l["component"] not in ("Taxable Pay", "NSSF Employer", "Housing Levy Employer") for l in p["earnings"] + p["deductions"]))
		check("the preview saves nothing", frappe.db.count("Salary Slip") == before)
		nobody = frappe.get_doc({"doctype": "Employee", "first_name": "E2E No Pay", "gender": "Female", "date_of_birth": "1990-01-01",
		                         "date_of_joining": "2020-01-01", "company": COMPANY, "status": "Active"}).insert(ignore_permissions=True)
		expect_throw("an employee with no assignment is refused (HRMS)", lambda: hs.preview_salary_slip("FoLT Kenya Payroll", nobody.name, "2026-11-30"))

	as_user(HR_MANAGER, flow)
	expect_throw("an HR User cannot preview pay", lambda: as_user(HR_USER, lambda: hs.preview_salary_slip("FoLT Kenya Payroll", "HR-EMP-00001")), frappe.PermissionError)


def run():
	frappe.db.commit()  # start clean: everything below is rolled back
	try:
		who_gets_in()
		hr_user_reads_only()
		read_only_pass()
		hr_manager_keeps_settings()
		holidays()
		leave()
		pay_setup()
	finally:
		frappe.db.rollback()
		frappe.clear_cache()
	print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
	if FAIL:
		print("FAILED:", *FAIL, sep="\n  ")
	return {"passed": len(PASS), "failed": FAIL}
