"""End-to-end check of admin_portal (the Settings module's writes) and user_guard.

What this proves:

  - the floor: only System Manager and FoLT User Administrator reach the user calls; anyone else
    (a Head of Finance, a Security Administrator) is refused
  - the ceiling: a User Administrator cannot grant or remove a privileged role, cannot change a
    user who holds one (enable, roles, profile, reset, delete), cannot change their own roles, and
    cannot change a user type; a System Manager can do all of it
  - lock-out guards: nobody disables themselves or removes their own System Manager; the last
    enabled System Manager can be neither disabled nor stripped -- through the edge AND through a
    plain save (user_guard), which is what the Desk and REST do
  - refusals are answers: every refusal comes back as {"refused": reason} with message_log empty
  - create: a user with roles and an employee link, no password set, duplicate e-mail refused,
    Employee role without an employee refused
  - the ERP-undo guards (role profile, Employee without a link, approver still assigned)
  - every write leaves one "FoLT Settings: ..." Activity Log row naming the actor, and no secret
  - delete only without history; reset link sent through Frappe, never returned

THIS SUITE DELIBERATELY DOES NOT SET frappe.flags.in_test (message_log must be the live one).
Everything is made inside the run's transaction and rolled back. Mail is muted. Run with:

    bench --site <site> execute folt_customizations.admin_portal_e2e.run
"""

import json

import frappe

from folt_customizations import admin_portal as ap
from folt_customizations.user_guard import SYSTEM_MANAGER

USER_ADMIN = "useradmin.test@folt.test"
SECURITY_ADMIN = "secadmin.test@folt.test"
HEAD_OF_FINANCE = "hof.test@folt.test"
REQUESTER = "requester.test@folt.test"  # linked to an Employee
PURCHASER = "purchaser.test@folt.test"
PREFIX = "e2e-admin-"

PASS, FAIL = [], []


def check(label, condition, detail=""):
	(PASS if condition else FAIL).append(label)
	print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  — ' + str(detail)[:200] if detail and not condition else ''}")


def thrown(fn):
	try:
		fn()
	except Exception as e:  # noqa: BLE001
		return type(e).__name__
	return None


def as_user(user, fn, *args, **kwargs):
	frappe.set_user(user)
	frappe.local.message_log = []
	try:
		return fn(*args, **kwargs)
	finally:
		frappe.set_user("Administrator")


def refused(result, reason):
	return isinstance(result, dict) and result.get("refused") == reason


def quiet():
	return not frappe.local.message_log


def audit_rows(name, since):
	return frappe.get_all(
		"Activity Log",
		filters={"reference_doctype": "User", "reference_name": name, "subject": ["like", ap.AUDIT_PREFIX + "%"], "creation": [">=", since]},
		fields=["subject", "user", "content"],
	)


def make_sm(email):
	"""A second System Manager, so the real ones are never the subject of a lock-out test."""
	doc = frappe.get_doc({"doctype": "User", "email": email, "first_name": "E2E", "last_name": "SM", "send_welcome_email": 0, "user_type": "System User"})
	doc.append("roles", {"role": SYSTEM_MANAGER})
	doc.insert(ignore_permissions=True)
	return doc.name


def run():
	frappe.flags.mute_emails = True
	frappe.set_user("Administrator")
	since = frappe.utils.now_datetime()
	try:
		_run(since)
	finally:
		frappe.db.rollback()
		frappe.set_user("Administrator")
	print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
	if FAIL:
		print("FAILED:", *FAIL, sep="\n  ")


def _run(since):
	for u in (USER_ADMIN, SECURITY_ADMIN, HEAD_OF_FINANCE, REQUESTER):
		check(f"fixture user exists: {u}", frappe.db.exists("User", u))
	check("User Administrator holds one role", ap._user_roles(USER_ADMIN) == ["FoLT User Administrator"], ap._user_roles(USER_ADMIN))

	print("\n— the floor")
	for user in (HEAD_OF_FINANCE, SECURITY_ADMIN):
		check(f"{user} refused user_form", thrown(lambda u=user: as_user(u, ap.user_form)) == "PermissionError")
		check(f"{user} refused set_enabled", thrown(lambda u=user: as_user(u, ap.set_enabled, PURCHASER, 0)) == "PermissionError")
	form = as_user(USER_ADMIN, ap.user_form)
	check("User Administrator reads the form quietly", quiet() and form["roles"], len(form.get("roles", [])))
	sm_row = next((r for r in form["roles"] if r["name"] == SYSTEM_MANAGER), None)
	check("System Manager offered as privileged, not grantable", sm_row and sm_row["privileged"] and not sm_row["grantable"], sm_row)
	check("automatic roles never offered", not any(r["name"] in ap.AUTOMATIC_ROLES for r in form["roles"]))
	check("System Manager may grant anything", all(r["grantable"] for r in ap.user_form()["roles"]))

	print("\n— create")
	email = PREFIX + "new@folt.test"
	r = as_user(USER_ADMIN, ap.create_user, json.dumps({"email": email, "first_name": "E2E", "last_name": "New", "roles": ["Purchase User"], "send_welcome_email": 0}))
	check("User Administrator creates a user quietly", r.get("user") == email and quiet(), r)
	check("roles applied", "Purchase User" in ap._user_roles(email))
	check("no password was set", not frappe.db.sql("select 1 from `__Auth` where doctype='User' and name=%s and fieldname='password'", email))
	check("duplicate e-mail refused", refused(as_user(USER_ADMIN, ap.create_user, {"email": email, "first_name": "X"}), "duplicate"))
	check("privileged role at create refused", refused(as_user(USER_ADMIN, ap.create_user, {"email": PREFIX + "p@folt.test", "first_name": "X", "roles": [SYSTEM_MANAGER]}), "privileged_role") and quiet())
	check("Employee role without an employee refused", refused(as_user(USER_ADMIN, ap.create_user, {"email": PREFIX + "e@folt.test", "first_name": "X", "roles": ["Employee"]}), "employee_role_needs_employee"))
	check("Website User only by a System Manager", refused(as_user(USER_ADMIN, ap.create_user, {"email": PREFIX + "w@folt.test", "first_name": "X", "user_type": "Website User"}), "privileged_field"))
	check("unknown role refused", refused(as_user(USER_ADMIN, ap.create_user, {"email": PREFIX + "u@folt.test", "first_name": "X", "roles": ["No Such Role"]}), "unknown_role"))
	free = frappe.get_all("Employee", filters={"status": "Active", "user_id": ["is", "not set"]}, pluck="name", limit=1)
	if free:
		email2 = PREFIX + "emp@folt.test"
		r = as_user(USER_ADMIN, ap.create_user, {"email": email2, "first_name": "E2E", "roles": ["Employee"], "employee": free[0], "send_welcome_email": 0})
		check("create with an employee links it", r.get("employee") and r["employee"]["name"] == free[0], r)
		check("...and the Employee role stays", "Employee" in ap._user_roles(email2))
	check("create audited by the actor", any(a.user == USER_ADMIN and a.subject.endswith("user created") for a in audit_rows(email, since)))

	print("\n— the ceiling")
	sm2 = make_sm(PREFIX + "sm2@folt.test")
	check("User Administrator cannot grant System Manager", refused(as_user(USER_ADMIN, ap.change_roles, email, [SYSTEM_MANAGER], []), "privileged_role"))
	check("...nor Accounts Manager", refused(as_user(USER_ADMIN, ap.change_roles, email, ["Accounts Manager"], []), "privileged_role"))
	check("...nor disable a System Manager", refused(as_user(USER_ADMIN, ap.set_enabled, sm2, 0), "privileged_user"))
	check("...nor edit one", refused(as_user(USER_ADMIN, ap.update_user, sm2, {"first_name": "X"}), "privileged_user"))
	check("...nor reset one's password", refused(as_user(USER_ADMIN, ap.send_password_reset, sm2), "privileged_user"))
	check("...nor change their own roles", refused(as_user(USER_ADMIN, ap.change_roles, USER_ADMIN, ["Purchase User"], []), "self_roles"))
	check("...nor set a user type", refused(as_user(USER_ADMIN, ap.update_user, email, {"user_type": "Website User"}), "privileged_field"))
	check("...nor touch Administrator", refused(as_user(USER_ADMIN, ap.set_enabled, "Administrator", 0), "protected_user"))
	check("ceiling refusals left no messages", quiet())
	r = as_user(USER_ADMIN, ap.change_roles, email, ["Purchase Manager"], ["Purchase User"])
	check("User Administrator changes ordinary roles", r.get("changed") and "Purchase Manager" in r["after"] and "Purchase User" not in r["after"], r)
	r = ap.change_roles(email, ["Accounts Manager"], [])
	check("System Manager grants a privileged role", r.get("changed"), r)
	check("...after which the User Administrator cannot change that user", refused(as_user(USER_ADMIN, ap.set_enabled, email, 0), "privileged_user"))
	ap.change_roles(email, [], ["Accounts Manager"])

	print("\n— profile, enable, stale")
	doc = frappe.get_doc("User", email)
	r = as_user(USER_ADMIN, ap.update_user, email, {"first_name": "Renamed", "mobile_no": "+254700000001"}, str(doc.modified))
	check("profile edited", frappe.db.get_value("User", email, "first_name") == "Renamed", r)
	check("stale modified refused", refused(as_user(USER_ADMIN, ap.update_user, email, {"first_name": "X"}, "2000-01-01 00:00:00"), "stale"))
	check("unlisted field refused", refused(as_user(USER_ADMIN, ap.update_user, email, {"api_key": "x"}), "field_not_allowed"))
	r = as_user(USER_ADMIN, ap.set_enabled, email, 0)
	check("disabled", r.get("changed") and not frappe.db.get_value("User", email, "enabled"), r)
	check("reset refused for a disabled user", refused(as_user(USER_ADMIN, ap.send_password_reset, email), "user_disabled"))
	as_user(USER_ADMIN, ap.set_enabled, email, 1)
	check("nobody disables themselves", refused(as_user(USER_ADMIN, ap.set_enabled, USER_ADMIN, 0), "self_disable"))

	print("\n— password reset")
	r = as_user(USER_ADMIN, ap.send_password_reset, email)
	check("reset sent, link never returned", r.get("sent") and "update-password" not in json.dumps(r), r)
	check("reset key stored", frappe.db.get_value("User", email, "reset_password_key"))

	print("\n— the last System Manager")
	others = frappe.db.sql(
		"""select distinct u.name from `tabUser` u join `tabHas Role` r on r.parent=u.name and r.parenttype='User'
		where r.role=%s and u.enabled=1 and u.name not in ('Administrator','Guest') and u.name != %s""",
		(SYSTEM_MANAGER, sm2),
		pluck=True,
	)
	for name in others:  # leave sm2 the only one, inside the transaction
		frappe.db.set_value("User", name, "enabled", 0, update_modified=False)
	check("sm2 is now the last System Manager", not ap.other_system_managers(sm2))
	check("edge refuses disabling the last", refused(ap.set_enabled(sm2, 0), "last_system_manager"))
	check("edge refuses stripping the last", refused(ap.change_roles(sm2, [], [SYSTEM_MANAGER]), "last_system_manager"))
	frappe.local.message_log = []
	doc = frappe.get_doc("User", sm2)
	doc.enabled = 0
	check("a plain save (Desk / REST) is refused too", thrown(lambda: doc.save(ignore_permissions=True)) == "PermissionError")
	frappe.local.message_log = []
	frappe.set_user(sm2)
	check("nobody removes their own System Manager", refused(ap.change_roles(sm2, [], [SYSTEM_MANAGER]), "self_system_manager"))
	frappe.set_user("Administrator")
	for name in others:
		frappe.db.set_value("User", name, "enabled", 1, update_modified=False)

	print("\n— ERP-undo guards")
	check("Employee role on an unlinked user refused", refused(ap.change_roles(email, ["Employee"], []), "employee_role_needs_employee"))
	approver = frappe.db.get_value("Employee", {"leave_approver": ["is", "set"]}, "leave_approver")
	if approver and approver not in ("Administrator",) and "Leave Approver" in ap._user_roles(approver):
		check("Leave Approver kept while still an approver", refused(ap.change_roles(approver, [], ["Leave Approver"]), "approver_role_assigned"))
	if frappe.db.exists("Role Profile", "Purchase"):
		u = frappe.get_doc("User", email)
		u.append("role_profiles", {"role_profile": "Purchase"})
		u.save(ignore_permissions=True)
		check("role-profile user refused", refused(ap.change_roles(email, ["Purchase User"], []), "role_profile_managed"))

	print("\n— employee link")
	if free:
		r = ap.unlink_employee(PREFIX + "emp@folt.test")
		check("unlinked", r.get("employee") is None and not frappe.db.get_value("Employee", free[0], "user_id"), r)
		r = as_user(USER_ADMIN, ap.link_employee, PREFIX + "emp@folt.test", free[0])
		check("linked by a User Administrator", r.get("employee") and r["employee"]["name"] == free[0], r)
	linked = frappe.db.get_value("Employee", {"user_id": REQUESTER}, "name")
	if linked:
		check("an employee linked elsewhere is refused", refused(ap.link_employee(email, linked), "employee_linked"))

	print("\n— delete and history")
	check("a user with history is kept", refused(ap.delete_user(REQUESTER), "has_history"))
	fresh = PREFIX + "fresh@folt.test"
	ap.create_user({"email": fresh, "first_name": "Fresh", "send_welcome_email": 0})
	r = as_user(USER_ADMIN, ap.delete_user, fresh)
	check("a user without history is deleted", r.get("deleted") and not frappe.db.exists("User", fresh), r)
	r = ap.user_activity(REQUESTER, "logins")
	check("login history reads", isinstance(r.get("items"), list), r)
	r = ap.user_activity(REQUESTER, "changes")
	check("change history carries no values", all(set(i) == {"at", "what", "status", "ip", "detail"} for i in r["items"]), r)
	p = as_user(USER_ADMIN, ap.user_profile, REQUESTER)
	check("profile shows the linked employee", p.get("employee") and p["employee"]["name"] == linked, p)

	print("\n— audit")
	rows = audit_rows(email, since)
	check("every write on the user recorded", len(rows) >= 6, [r.subject for r in rows])
	check("no secret in any record", not any("update-password" in (r.content or "") or "api_secret" in (r.content or "") for r in rows))
	check("delete recorded", frappe.db.exists("Activity Log", {"subject": f"{ap.AUDIT_PREFIX}user deleted ({fresh})", "user": USER_ADMIN}))
