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


def run():
	frappe.db.commit()  # start clean: everything below is rolled back
	try:
		who_gets_in()
		hr_user_reads_only()
		read_only_pass()
		hr_manager_keeps_settings()
		holidays()
	finally:
		frappe.db.rollback()
		frappe.clear_cache()
	print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
	if FAIL:
		print("FAILED:", *FAIL, sep="\n  ")
	return {"passed": len(PASS), "failed": FAIL}
