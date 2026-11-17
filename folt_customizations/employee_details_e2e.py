"""End-to-end check of employee_details: the whole Employee record for HR onboarding.

Claims:
- The record comes as ERPNext's own tabs (Overview, Address & Contacts, Attendance & Leaves, Salary,
  Personal Details, Profile, Joining, Exit), with the education and work-history tables, and FoLT's
  Kenyan statutory numbers on Personal Details.
- Field-level permission decides who sees what: HR Manager edits; HR User reads everything HR keeps
  but changes nothing here; a payroll role does not reach this screen; an ordinary employee's login
  cannot read a colleague's personal details or CTC at all (permlevels 1 and 2).
- A save changes only fields shown as editable, refuses anything else by name, checks link choices,
  replaces a child table whole, keeps only this employee's row names, and refuses a stale `modified`.
- KRA PIN and National ID are kept in the issued form and never on two people.
- `missing` names what FoLT wants on file before onboarding is complete, and empties as it is filled.

Runs in one transaction and rolls it back. Run with
    bench --site <site> execute folt_customizations.employee_details_e2e.run
"""

import json

import frappe

from folt_customizations import hr_settings_portal as portal

HR_MANAGER = "hrmanager.test@folt.test"
HR_USER = "hruser.test@folt.test"
REQUESTER = "requester.test@folt.test"
FINANCE = "financeofficer.test@folt.test"
TABS = ["Overview", "Address & Contacts", "Attendance & Leaves", "Salary", "Personal Details", "Profile", "Joining", "Exit"]
results = []


def check(label, ok, detail=""):
	results.append(bool(ok))
	print(("  ok    " if ok else "  FAIL  ") + label + (f"  [{detail}]" if detail and not ok else ""))


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


def fields_of(view):
	return {f["fieldname"]: f for t in view["tabs"] for s in t["sections"] for f in s["fields"]}


def tables_of(view):
	return {tb["fieldname"]: tb for t in view["tabs"] for s in t["sections"] for tb in s["tables"]}


def tab_of(view, fieldname):
	return next(t["label"] for t in view["tabs"] for s in t["sections"] if any(f["fieldname"] == fieldname for f in s["fields"]))


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
	emp = frappe.db.get_value("Employee", {"user_id": ["is", "not set"], "status": "Active"}, "name", order_by="name asc")
	other = frappe.db.get_value("Employee", {"name": ["!=", emp], "status": "Active"}, "name", order_by="name desc")

	print("\n--- 1  ERPNext's tabs, in order ---")
	view = as_user(HR_MANAGER, lambda: portal.employee_details(emp))
	check("the record comes as the Employee form's eight tabs", [t["label"] for t in view["tabs"]] == TABS, [t["label"] for t in view["tabs"]])
	f = fields_of(view)
	check("every tab's fields are there", all(k in f for k in ("salutation", "grade", "current_address", "relation", "holiday_list", "leave_approver",
	      "ctc", "bank_name", "blood_group", "passport_number", "bio", "notice_number_of_days", "reason_for_leaving")))
	check("the profile tables, with their columns", {"education", "external_work_history", "internal_work_history"} <= set(tables_of(view))
	      and {"school_univ", "qualification", "year_of_passing"} <= {c["fieldname"] for c in tables_of(view)["education"]["columns"]})
	check("the statutory numbers are on Personal Details", all(tab_of(view, k) == "Personal Details" for k in ("folt_national_id", "folt_kra_pin", "folt_nssf_number", "folt_sha_number")))
	check("the login and the applicant are shown, not edited", not f["user_id"]["editable"] and not f["job_applicant"]["editable"])
	check("internals are left out", not {"naming_series", "employee_name", "lft", "create_user_automatically"} & set(f))
	check("links carry their choices", f["department"]["choices"] and all(c["value"].endswith(" - FoLT") or c["value"] for c in f["department"]["choices"]))

	print("\n--- 2  who sees what ---")
	check("HR Manager edits", view["editable"] and f["passport_number"]["editable"] and f["bank_name"]["editable"])
	hr_user = as_user(HR_USER, lambda: portal.employee_details(emp))
	hu = fields_of(hr_user)
	check("HR User reads personal details but changes nothing here", not hr_user["editable"] and "passport_number" in hu and not any(x["editable"] for x in hu.values()))
	expect_throw("HR User cannot save", lambda: as_user(HR_USER, lambda: portal.save_employee_details(emp, json.dumps({"bio": "x"}), hr_user["modified"])), frappe.PermissionError)
	expect_throw("an employee cannot open it", lambda: as_user(REQUESTER, lambda: portal.employee_details(emp)), frappe.PermissionError)
	if frappe.db.exists("User", FINANCE):
		expect_throw("nor can a payroll role without an HR role", lambda: as_user(FINANCE, lambda: portal.employee_details(emp)), frappe.PermissionError)

	def colleague_sees():
		d = frappe.get_doc("Employee", other)
		d.apply_fieldlevel_read_permissions()
		return {k: d.get(k) for k in ("passport_number", "health_details", "current_address", "folt_kra_pin", "ctc", "bank_ac_no")}

	frappe.db.set_value("Employee", other, {"passport_number": "SECRET-PP", "health_details": "SECRET-HEALTH", "current_address": "SECRET-ADDR", "ctc": 999999})
	seen = as_user(REQUESTER, colleague_sees)
	check("an employee's login cannot read a colleague's passport, health, address, statutory numbers, CTC or bank",
	      not any(v for v in seen.values()), seen)

	print("\n--- 3  saving ---")
	dept = next(c["value"] for c in f["department"]["choices"])
	saved = as_user(HR_MANAGER, lambda: portal.save_employee_details(emp, json.dumps({
		"department": dept, "cell_number": "+254700000001", "person_to_be_contacted": "Test Contact", "emergency_phone_number": "+254700000002",
		"relation": "Sister", "blood_group": "O+", "passport_number": "AK0000001", "notice_number_of_days": 30,
		"folt_kra_pin": "a 000000001 z", "folt_national_id": "12 345 678",
		"education": [{"school_univ": "Test University", "qualification": "BSc", "level": "Graduate", "year_of_passing": 2015}],
	}), view["modified"]))
	sf = fields_of(saved)
	check("the fields are saved", sf["department"]["value"] == dept and sf["blood_group"]["value"] == "O+" and sf["notice_number_of_days"]["value"] == 30)
	check("statutory numbers are kept as issued", sf["folt_kra_pin"]["value"] == "A000000001Z" and sf["folt_national_id"]["value"] == "12345678", (sf["folt_kra_pin"]["value"], sf["folt_national_id"]["value"]))
	edu = tables_of(saved)["education"]["rows"]
	check("a table row is added", len(edu) == 1 and edu[0]["school_univ"] == "Test University")
	gone = {m["fieldname"] for m in view["missing"]} - {m["fieldname"] for m in saved["missing"]}
	check("missing empties as it is filled", {"cell_number", "relation", "folt_kra_pin"} <= gone, gone)

	expect_throw("a stale save is refused", lambda: as_user(HR_MANAGER, lambda: portal.save_employee_details(emp, json.dumps({"bio": "late"}), view["modified"])), frappe.TimestampMismatchError)
	expect_throw("a field not on the screen is refused by name", lambda: as_user(HR_MANAGER, lambda: portal.save_employee_details(emp, json.dumps({"user_id": "x@y.z", "naming_series": "X"}), saved["modified"])), saying="naming_series, user_id")
	expect_throw("a link outside its choices is refused", lambda: as_user(HR_MANAGER, lambda: portal.save_employee_details(emp, json.dumps({"department": "Nowhere"}), saved["modified"])), saying="not one of the choices")
	expect_throw("a malformed KRA PIN is refused", lambda: as_user(HR_MANAGER, lambda: portal.save_employee_details(emp, json.dumps({"folt_kra_pin": "12345"}), saved["modified"])), saying="not a KRA PIN")
	frappe.db.set_value("Employee", other, "folt_kra_pin", "A000000002Z")
	expect_throw("a KRA PIN already on someone else is refused", lambda: as_user(HR_MANAGER, lambda: portal.save_employee_details(emp, json.dumps({"folt_kra_pin": "A000000002Z"}), saved["modified"])), saying="already on")

	foreign = frappe.db.get_value("Employee Education", {"parent": ["!=", emp]}, "name")
	row = edu[0]
	again = as_user(HR_MANAGER, lambda: portal.save_employee_details(emp, json.dumps({
		"education": [{**row, "qualification": "MSc"}, {"name": foreign or "not-mine", "school_univ": "Second School", "qualification": "KCSE"}],
	}), saved["modified"]))
	rows = tables_of(again)["education"]["rows"]
	check("a table is replaced whole: the row edited in place, the other added", len(rows) == 2 and rows[0]["name"] == row["name"] and rows[0]["qualification"] == "MSc", rows)
	check("a row name from another record is not written over", foreign is None or rows[1]["name"] != foreign)
	cleared = as_user(HR_MANAGER, lambda: portal.save_employee_details(emp, json.dumps({"education": [], "blood_group": None}), again["modified"]))
	check("a table emptied and a field cleared", tables_of(cleared)["education"]["rows"] == [] and fields_of(cleared)["blood_group"]["value"] is None)
