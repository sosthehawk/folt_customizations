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
# Documents whose save locks them and queues work after commit (RoleProfile / ModuleProfile).
LOCKED_BY_RUN = (("Role Profile", "E2E Profile"), ("Role Profile", "E2E SM Profile"), ("Module Profile", "E2E Modules"))

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
	# Nothing here may reach the database for good: some Frappe calls commit on their own
	# (frappe.sessions.delete_session does), which once committed a whole run's fixtures. So
	# commit is a no-op for the run, and every attempt is reported.
	real_commit, commits = frappe.db.commit, []
	frappe.db.commit = lambda *a, **k: commits.append(1)
	try:
		_run(since)
		_run_phase2()
		_run_phase3()
		check("the run committed nothing (delete_session's commit was held back)", len(commits) <= 1, f"{len(commits)} commit attempts")
	finally:
		frappe.db.commit = real_commit
		frappe.db.rollback()
		# Saving a role or module profile locks it and queues the users' rebuild after commit; with
		# no commit the job never runs, so the run releases the locks it took itself.
		from frappe.utils.file_lock import delete_lock, release_document_locks

		release_document_locks()
		for doctype, name in LOCKED_BY_RUN:
			delete_lock(frappe.get_doc({"doctype": doctype, "name": name}).get_signature())
		frappe.set_user("Administrator")
		# set_global goes through the defaults cache: a rolled-back store must not linger in Redis.
		frappe.clear_cache()
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


def _run_phase2():
	from folt_customizations import permissions as perms

	print("\n— roles")
	check("only a System Manager creates roles", thrown(lambda: as_user(USER_ADMIN, ap.create_role, {"role_name": "E2E Role"})) == "PermissionError")
	r = ap.create_role({"role_name": "E2E Role", "desk_access": 1})
	check("custom role created, granting nothing", r.get("custom") and r["editable"] and not r["permissions"], r)
	check("duplicate role refused", refused(ap.create_role({"role_name": "E2E Role"}), "duplicate"))
	r = ap.update_role("E2E Role", {"two_factor_auth": 1})
	check("custom role edited", r.get("two_factor_auth"), r)
	check("standard role not edited", refused(ap.update_role("Purchase User", {"disabled": 1}), "standard_role"))
	check("privileged custom role not edited", refused(ap.update_role("FoLT User Administrator", {"disabled": 1}), "standard_role"))
	d = as_user(USER_ADMIN, ap.role_detail, "Purchase User")
	check("User Administrator reads a role with its users", isinstance(d.get("users"), list) and not d["editable"], d.get("editable"))

	print("\n— role profiles")
	p = ap.save_role_profile("E2E Profile", ["Purchase User", "Employee"], 1)
	check("role profile created", p.get("roles") == ["Employee", "Purchase User"], p)
	check("privileged role profile marked", ap.save_role_profile("E2E SM Profile", [SYSTEM_MANAGER], 1).get("privileged"))
	email = PREFIX + "prof@folt.test"
	ap.create_user({"email": email, "first_name": "Prof", "send_welcome_email": 0})
	r = as_user(USER_ADMIN, ap.set_user_role_profiles, email, ["E2E Profile"])
	check("User Administrator gives an ordinary profile", r.get("changed") and "Purchase User" in ap._user_roles(email), r)
	check("...not a privileged one", refused(as_user(USER_ADMIN, ap.set_user_role_profiles, email, ["E2E SM Profile"]), "privileged_role"))

	print("\n— the permission matrix")
	check("only a System Manager edits permissions", thrown(lambda: as_user(USER_ADMIN, ap.set_permission, "Note", "Purchase User", 0, "read", 1)) == "PermissionError")
	r = ap.set_permission("Purchase Order", "Finance Manager", 0, "submit", 0)
	check("a code-managed right revoked", r.get("changed"), r)
	perms.apply_role_permissions()
	row = next(x for x in frappe.get_meta("Purchase Order").permissions if x.role == "Finance Manager" and x.permlevel == 0)
	check("...and the migrate re-apply leaves it revoked", not row.submit)
	src = ap.permission_sources(["Purchase Order"])["Purchase Order"]
	check("the revoke shows as an override", {"role": "Finance Manager", "permlevel": 0, "ptype": "submit", "source": "revoked"} in src, src)
	r = ap.set_permission("Note", "E2E Role", 0, "read", 1)
	check("a new rule granted with read only", r.get("changed") and not next(x for x in frappe.get_meta("Note").permissions if x.role == "E2E Role").export)
	check("System Manager keeps User read", refused(ap.set_permission("User", SYSTEM_MANAGER, 0, "read", 0), "protected_permission"))
	check("no write on User for another role", refused(ap.set_permission("User", "E2E Role", 0, "write", 1), "protected_permission"))
	check("no write for Guest", refused(ap.set_permission("Note", "Guest", 0, "write", 1), "protected_permission"))
	check("no submit on a non-submittable doctype", refused(ap.set_permission("Note", "E2E Role", 0, "submit", 1), "not_submittable"))
	check("child tables refused", refused(ap.set_permission("Has Role", "E2E Role", 0, "read", 1), "child_table"))
	ap.reset_permissions("Purchase Order")
	row = next(x for x in frappe.get_meta("Purchase Order").permissions if x.role == "Finance Manager" and x.permlevel == 0)
	check("reset restores FoLT's grants and drops the override", row.submit and "Purchase Order" not in perms.permission_overrides())
	check("matrix edits audited", frappe.db.exists("Activity Log", {"subject": ap.AUDIT_PREFIX + "permission changed", "reference_name": "Purchase Order"}))

	print("\n— user permissions")
	company = frappe.get_all("Company", pluck="name", limit=1)[0]
	r = as_user(USER_ADMIN, ap.add_user_permission, email, "Company", company)
	check("User Administrator restricts a user to a company", len(r.get("rows", [])) == 1, r)
	check("duplicate refused", refused(ap.add_user_permission(email, "Company", company), "duplicate"))
	check("unlisted doctype refused", refused(ap.add_user_permission(email, "Salary Slip", "x"), "invalid_permission"))
	r = as_user(USER_ADMIN, ap.remove_user_permission, r["rows"][0]["name"])
	check("restriction removed", r.get("rows") == [], r)

	print("\n— Desk access")
	check("Desk access is a System Manager's", thrown(lambda: as_user(USER_ADMIN, ap.desk_access)) == "PermissionError")
	ws = next(w for w in ap.desk_access()["workspaces"] if not w["hidden"])
	ap.set_workspace_visibility(ws["name"], 1)
	from folt_customizations.workspaces import hidden_workspaces

	check("hiding a workspace records it where migrate keeps it", ws["name"] in hidden_workspaces())
	ap.set_workspace_visibility(ws["name"], 0)
	check("showing it drops it from the store", ws["name"] not in hidden_workspaces())
	mods = [m["value"] for m in ap.desk_access()["modules"]][:2]
	r = ap.save_module_profile("E2E Modules", mods, 1)
	check("module profile saved", any(p["name"] == "E2E Modules" and p["blocked"] == sorted(mods) for p in r["module_profiles"]))

	print("\n— sessions")
	s = as_user(SECURITY_ADMIN, ap.sessions)
	check("Security Administrator lists sessions", isinstance(s.get("sessions"), list))
	check("no sid or csrf token leaves", "csrf" not in frappe.as_json(s) and "_sid" not in frappe.as_json(s))
	check("others refused", thrown(lambda: as_user(HEAD_OF_FINANCE, ap.sessions)) == "PermissionError")
	frappe.db.sql("insert into `tabSessions` (user, sid, sessiondata, lastupdate, status) values (%s, 'e2e-sid-1', '{}', now(), 'Active')", email)
	handle = next(x["handle"] for x in ap.sessions(email)["sessions"])
	r = as_user(USER_ADMIN, ap.revoke_sessions, handle)
	check("a session signed out by handle", r.get("revoked") == 1 and not frappe.db.exists("Sessions", {"sid": "e2e-sid-1"}), r)
	check("a privileged user's sessions are a System Manager's", refused(as_user(SECURITY_ADMIN, ap.revoke_sessions, None, "Administrator"), "protected_user"))
	check("phase 2 left no messages", not frappe.local.message_log, frappe.local.message_log)


def _run_phase3():
	print("\n— System Settings by who asks")
	ss = frappe.get_doc("System Settings")
	r = as_user(SECURITY_ADMIN, ap.update_settings, {"minimum_password_score": "3", "allow_login_after_fail": 120})
	check("Security Administrator changes security settings", r.get("changed") == ["allow_login_after_fail", "minimum_password_score"] and quiet(), r)
	check("...and the value is saved", frappe.db.get_single_value("System Settings", "minimum_password_score") == "3")
	check("...not regional ones", refused(as_user(SECURITY_ADMIN, ap.update_settings, {"date_format": "yyyy-mm-dd"}), "privileged_field"))
	check("...nor unlisted ones", refused(as_user(SECURITY_ADMIN, ap.update_settings, {"encryption_key": "x"}), "setting_not_allowed"))
	check("User Administrator refused settings", thrown(lambda: as_user(USER_ADMIN, ap.update_settings, {"session_expiry": "10:00"})) == "PermissionError")
	r = ap.update_settings({"date_format": "yyyy-mm-dd", "time_zone": "Africa/Nairobi", "currency_precision": "2"})
	check("System Manager changes regional settings", r.get("changed") == ["currency_precision", "date_format", "time_zone"], r)
	check("unknown time zone refused", refused(ap.update_settings({"time_zone": "Mars/Base"}), "invalid_setting"))
	check("unknown choice refused", refused(ap.update_settings({"date_format": "mm/yy"}), "invalid_setting"))
	check("unknown country refused", refused(ap.update_settings({"country": "Atlantis"}), "invalid_setting"))
	check("stale modified refused", refused(ap.update_settings({"backup_limit": 3}, "2000-01-01 00:00:00"), "stale"))
	if not ap._other_sign_in():
		check("passwords stay on when nothing else can sign anyone in", refused(ap.update_settings({"disable_user_pass_login": 1}), "no_other_sign_in"))
	meta = as_user(SECURITY_ADMIN, ap.settings_meta)
	check("Security Administrator reads the settings meta", any(f["fieldname"] == "session_expiry" for f in meta))
	check("settings audited", frappe.db.exists("Activity Log", {"subject": ap.AUDIT_PREFIX + "settings changed", "user": SECURITY_ADMIN}))
	del ss

	print("\n— two-factor and sign-in restrictions")
	r = as_user(SECURITY_ADMIN, ap.set_role_two_factor, "Purchase User", 1)
	check("Security Administrator requires 2FA of a role", r.get("two_factor_auth") is True, r)
	check("not of Guest", refused(ap.set_role_two_factor("Guest", 1), "standard_role"))
	rows = as_user(SECURITY_ADMIN, ap.role_two_factor)
	check("Security Administrator lists roles' two-factor", any(r["name"] == "Purchase User" and r["two_factor_auth"] for r in rows) and not any(r["name"] == "Guest" for r in rows))
	email = PREFIX + "sec@folt.test"
	ap.create_user({"email": email, "first_name": "Sec", "roles": ["Purchase User"], "send_welcome_email": 0})
	v = as_user(SECURITY_ADMIN, ap.user_security, email)
	check("user security reads", v.get("can_change") and "Purchase User" in v["two_factor_roles"], v)
	r = as_user(SECURITY_ADMIN, ap.update_user_security, email, {"restrict_ip": "10.0.0.0, 192.168.1.", "login_after": 6, "login_before": 20})
	check("IP and hours restricted", r.get("restrict_ip") == "10.0.0.0,192.168.1." and r["login_after"] == 6, r)
	check("bad hour refused", refused(ap.update_user_security(email, {"login_before": 25}), "invalid_setting"))
	check("bad IP refused", refused(ap.update_user_security(email, {"restrict_ip": "not an ip;"}), "invalid_setting"))
	check("nobody restricts themselves", refused(as_user(SECURITY_ADMIN, ap.update_user_security, SECURITY_ADMIN, {"login_after": 6}), "self_security"))
	sm = frappe.db.sql("""select u.name from tabUser u join `tabHas Role` r on r.parent=u.name and r.parenttype='User'
		where r.role='System Manager' and u.enabled=1 and u.name not in ('Administrator','Guest') limit 1""", pluck=True)
	if sm:
		check("a System Manager is a System Manager's to restrict", refused(as_user(SECURITY_ADMIN, ap.update_user_security, sm[0], {"login_after": 6}), "privileged_user"))
	frappe.db.set_single_value("System Settings", "enable_two_factor_auth", 1)
	from frappe.twofactor import set_default as set_2fa_default

	set_2fa_default(email + "_otpsecret", "x")
	r = as_user(SECURITY_ADMIN, ap.reset_user_otp, email)
	check("authenticator reset by a Security Administrator", r.get("authenticator_set") is False, r)

	print("\n— the organisation")
	check("the organisation is a System Manager's", thrown(lambda: as_user(SECURITY_ADMIN, ap.organisation)) == "PermissionError")
	org = ap.organisation()
	check("organisation reads", org["companies"] and org["departments"] and org["countries"], {k: len(v) for k, v in org.items()})
	company = org["companies"][0]
	r = ap.update_company(company["name"], {"tax_id": "P051234567Z", "website": "https://folt.example"})
	check("company details saved", next(c for c in r["companies"] if c["name"] == company["name"])["tax_id"] == "P051234567Z")
	check("company name not changed here", refused(ap.update_company(company["name"], {"company_name": "X"}), "field_not_allowed"))
	if company["currency_locked"]:
		other = next((c for c in org["currencies"] if c != company["default_currency"]), None)
		if other:
			check("currency locked once ledger entries exist", refused(ap.update_company(company["name"], {"default_currency": other}), "currency_locked"))
	r = ap.save_org_record("Designation", {"designation_name": "E2E Designation", "description": "test"})
	check("designation created", any(d["name"] == "E2E Designation" for d in r["designations"]))
	r = ap.save_org_record("Designation", {"description": "changed"}, "E2E Designation")
	check("designation edited", next(d for d in r["designations"] if d["name"] == "E2E Designation")["description"] == "changed")
	check("a designation is not renamed", refused(ap.save_org_record("Designation", {"designation_name": "Other"}, "E2E Designation"), "field_not_allowed"))
	r = ap.save_org_record("Branch", {"branch": "E2E Branch"})
	check("branch created", any(b["name"] == "E2E Branch" for b in r["branches"]))
	check("duplicate branch refused", refused(ap.save_org_record("Branch", {"branch": "E2E Branch"}), "duplicate"))
	r = ap.delete_org_record("Branch", "E2E Branch")
	check("unused branch deleted", not any(b["name"] == "E2E Branch" for b in r["branches"]))
	used = next((d for d in org["designations"] if d["employees"]), None)
	if used:
		check("designation in use kept", refused(ap.delete_org_record("Designation", used["name"]), "in_use"))
	r = ap.save_org_record("Department", {"department_name": "E2E Dept", "company": company["name"], "parent_department": "All Departments"})
	dept = next((d for d in r["departments"] if d["label"] == "E2E Dept"), None)
	check("department created", dept is not None)
	if dept:
		r = ap.save_org_record("Department", {"disabled": 1}, dept["name"])
		check("department disabled", next(d for d in r["departments"] if d["name"] == dept["name"])["disabled"])
	r = ap.create_fiscal_year("E2E 2099", "2099-01-01", "2099-12-31", [company["name"]])
	check("fiscal year created", any(y["name"] == "E2E 2099" for y in r["fiscal_years"]))
	check("overlapping fiscal year refused by Frappe", thrown(lambda: ap.create_fiscal_year("E2E 2099b", "2099-06-01", "2100-05-31", [company["name"]])) is not None)
	frappe.local.message_log = []

	print("\n— my preferences")
	p = as_user(REQUESTER, ap.my_preferences)
	check("anyone reads their own preferences", p.get("user") == REQUESTER and "notifications" in p and p["time_zones"])
	r = as_user(REQUESTER, ap.save_my_preferences, {"desk_theme": "Dark", "notifications": {"enable_email_mention": 0}})
	check("own theme and e-mails saved", r.get("desk_theme") == "Dark" and r["notifications"]["enable_email_mention"] is False, r)
	check("nothing else is changed here", refused(as_user(REQUESTER, ap.save_my_preferences, {"roles": ["System Manager"]}), "field_not_allowed"))
	check("a bad time zone refused", refused(as_user(REQUESTER, ap.save_my_preferences, {"time_zone": "Mars/Base"}), "invalid_setting"))
	check("phase 3 left no messages", not frappe.local.message_log, frappe.local.message_log)
