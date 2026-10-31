"""The app's Settings module, server half (folt-workflow-ui /system; BFF package `system`).

Administration in the app is Frappe's own: a user is Frappe's User saved with Frappe's validation,
a password reset is Frappe's reset mail, a role is a Has Role row. What this module adds is the
edge every administrative WRITE goes through, so that the rules hold whoever is asking and from
wherever they ask:

*Who.* System Manager may do everything here. Two FoLT roles take a part of it each (separation of
duties): FoLT User Administrator manages people -- create, edit, enable, roles, employee links,
password resets -- and FoLT Security Administrator (security settings; later phases). The
delegated roles hold READ on User, Role and the profiles (permissions.ADMIN_PERMISSIONS) and no
Desk write at all, so the Desk cannot be used to step around the rules below; their writes are
made here, with ignore_permissions, only after the rules pass.

*The ceiling.* PRIVILEGED_ROLES are granted and removed only by a System Manager, and a user who
holds one is changed only by a System Manager -- so a User Administrator can neither make
themselves (or a friend) an administrator nor disable one. Nobody changes Administrator or Guest.
Nobody disables themselves, removes their own System Manager, or -- delegated -- changes their
own roles. No change may leave the site without an enabled System Manager (here, and for every
other save path in user_guard).

*Refusals are answers.* A rule that refuses returns {"refused": <reason>, "message": <text>}
instead of throwing, so nothing reaches message_log and the BFF can answer with the reason the
spec names (map §2). Frappe's own validation (a bad e-mail, a taken username) still throws and
reaches the BFF as Frappe's words.

*Every write is recorded.* _record writes an Activity Log row ("FoLT Settings: ...") naming the
actor, the IP, the record and what changed. Activity Log is System Manager-read-only and nothing
here edits or deletes one.
"""

import frappe
from frappe import _
from frappe.utils import cint, cstr, validate_email_address

from folt_customizations.portal_common import parse_json as _json
from folt_customizations.user_guard import STANDARD_USERS, SYSTEM_MANAGER, other_system_managers

USER_ADMIN = "FoLT User Administrator"
SECURITY_ADMIN = "FoLT Security Administrator"
USER_ADMINS = (SYSTEM_MANAGER, USER_ADMIN)

# Granted, removed and held only under a System Manager's hand.
PRIVILEGED_ROLES = frozenset({SYSTEM_MANAGER, "Administrator", USER_ADMIN, SECURITY_ADMIN, "Accounts Manager", "HR Manager"})
# Frappe assigns these itself; they are never added or removed (guard.AutomaticRoles in the BFF).
AUTOMATIC_ROLES = frozenset({"Administrator", "Guest", "All", "Desk User"})
# Roles erpnext/hrms add or strip on save -- the guards below refuse a change the ERP would undo.
EMPLOYEE_ROLES = ("Employee", "Employee Self Service")
APPROVER_FIELDS = {"Leave Approver": "leave_approver", "Expense Approver": "expense_approver"}
PORTAL_ROLES = ("Supplier", "Customer")

MAX_ROLE_CHANGES = 50
AUDIT_PREFIX = "FoLT Settings: "

# User fields the Settings screens edit. SYSTEM_ONLY_FIELDS are permlevel 1 or change what kind
# of login it is, so only a System Manager sets them.
PROFILE_FIELDS = (
	"first_name",
	"middle_name",
	"last_name",
	"username",
	"mobile_no",
	"phone",
	"language",
	"time_zone",
	"module_profile",
	"default_workspace",
	"default_app",
)
SYSTEM_ONLY_FIELDS = ("user_type",)
USER_TYPES = ("System User", "Website User")


# --- who is asking -----------------------------------------------------------------------


def _roles():
	return set(frappe.get_roles())


def _is_system_manager():
	return frappe.session.user == "Administrator" or SYSTEM_MANAGER in _roles()


def _only(*roles):
	"""The floor: the caller holds one of roles (System Manager always does)."""
	if frappe.session.user == "Administrator" or _roles().intersection((SYSTEM_MANAGER, *roles)):
		return
	frappe.throw(_("Only an administrator can do this."), frappe.PermissionError)


def _refuse(reason, message):
	return {"refused": reason, "message": message}


def _same(a, b):
	return cstr(a).strip().lower() == cstr(b).strip().lower()


# --- the record of what was done ---------------------------------------------------------------

_SECRET_HINTS = ("password", "secret", "api_key", "token")


def _mask(values):
	if not isinstance(values, dict):
		return values
	return {k: ("•••" if any(h in k for h in _SECRET_HINTS) else v) for k, v in values.items()}


def _record(action, doctype=None, name=None, before=None, after=None):
	"""One Activity Log row per administrative write. Secrets never reach it."""
	lines = []
	before, after = _mask(before or {}), _mask(after or {})
	for key in sorted(set(before) | set(after)):
		old, new = before.get(key), after.get(key)
		if old != new:
			lines.append(f"{key}: {_text(old)} → {_text(new)}")
	if doctype and name and not frappe.db.exists(doctype, name):
		# Deleted: the row cannot link to it, so it names it instead.
		action, doctype, name = f"{action} ({name})", None, None
	frappe.get_doc(
		{
			"doctype": "Activity Log",
			"subject": AUDIT_PREFIX + action,
			"content": "\n".join(lines) or None,
			"status": "Success",
			"reference_doctype": doctype,
			"reference_name": name,
			"user": frappe.session.user,
			"full_name": frappe.utils.get_fullname(frappe.session.user),
			"ip_address": getattr(frappe.local, "request_ip", None),
		}
	).insert(ignore_permissions=True)


def _text(value):
	if value in (None, "", []):
		return "—"
	if isinstance(value, (list, tuple, set)):
		return ", ".join(sorted(cstr(v) for v in value))
	return cstr(value)


# --- reading a user ------------------------------------------------------------------------


def _user_roles(name):
	return frappe.get_all("Has Role", filters={"parenttype": "User", "parent": name}, pluck="role", order_by="idx asc")


def _target(user):
	"""The canonical User (Frappe matches names case-insensitively), or a refusal."""
	user = cstr(user).strip()
	if not user or len(user) > 140:
		return None, _refuse("invalid_user", _("Name a user."))
	name = frappe.db.get_value("User", user, "name")
	if not name:
		return None, _refuse("not_found", _("There is no user {0}.").format(user))
	if name in STANDARD_USERS:
		return None, _refuse("protected_user", _("Administrator and Guest are never changed here."))
	return frappe.get_doc("User", name), None


def _ceiling(doc):
	"""A delegated administrator may not touch a user who holds a privileged role."""
	if _is_system_manager():
		return None
	held = sorted(PRIVILEGED_ROLES.intersection(r.role for r in doc.roles))
	if held:
		return _refuse("privileged_user", _("Only a System Manager can change a user who holds {0}.").format(", ".join(held)))
	return None


def _stale(doc, modified):
	if modified and cstr(modified) != cstr(doc.modified):
		return _refuse("stale", _("Someone else changed this user since you opened it. Reload and try again."))
	return None


def _employee_of(user):
	row = frappe.db.get_value(
		"Employee",
		{"user_id": user},
		["name", "employee_name", "company", "department", "designation", "reports_to", "status"],
		as_dict=True,
	)
	if not row:
		return None
	row["reports_to_name"] = frappe.db.get_value("Employee", row.reports_to, "employee_name") if row.reports_to else None
	return dict(row)


# --- what the Add / Edit forms offer ------------------------------------------------------------


@frappe.whitelist(methods=["GET"])
def user_form():
	"""The choices the Add user and Edit user forms need, as the caller may use them: every
	enabled role (privileged ones marked, and grantable only by a System Manager), role profiles,
	module profiles, workspaces, apps, languages, and the employees no login is linked to yet."""
	_only(USER_ADMIN)
	sm = _is_system_manager()
	roles = []
	for r in frappe.get_all("Role", filters={"disabled": 0}, fields=["name", "desk_access", "is_custom"], order_by="name asc"):
		if r.name in AUTOMATIC_ROLES:
			continue
		privileged = r.name in PRIVILEGED_ROLES
		roles.append({"name": r.name, "desk_access": bool(r.desk_access), "custom": bool(r.is_custom), "privileged": privileged, "grantable": sm or not privileged})
	employees = [
		{"value": e.name, "label": e.employee_name, "company": e.company, "department": e.department, "designation": e.designation}
		for e in frappe.get_all(
			"Employee",
			filters={"status": "Active", "user_id": ["is", "not set"]},
			fields=["name", "employee_name", "company", "department", "designation"],
			order_by="employee_name asc",
		)
	]
	return {
		"system_manager": sm,
		"roles": roles,
		"role_profiles": frappe.get_all("Role Profile", pluck="name", order_by="name asc"),
		"module_profiles": frappe.get_all("Module Profile", pluck="name", order_by="name asc"),
		"workspaces": frappe.get_all("Workspace", filters={"public": 1, "is_hidden": 0}, pluck="name", order_by="name asc"),
		"apps": _apps(),
		"languages": frappe.get_all("Language", filters={"enabled": 1}, fields=["name as value", "language_name as label"], order_by="language_name asc"),
		"user_types": list(USER_TYPES),
		"employees": employees,
		"companies": frappe.get_all("Company", pluck="name", order_by="name asc"),
		"departments": frappe.get_all("Department", filters={"is_group": 0}, pluck="name", order_by="name asc"),
	}


def _apps():
	from frappe.apps import get_apps

	try:
		return [{"value": a["name"], "label": a.get("title") or a["name"]} for a in get_apps()]
	except Exception:  # noqa: BLE001 -- the list is a convenience; the field still validates
		return []


# --- the directory's organisation columns -------------------------------------------------------


@frappe.whitelist(methods=["GET"])
def employee_links(users=None, company=None, department=None):
	"""{user: {employee, employee_name, company, department, designation}} for the users given,
	or -- with company and/or department -- for every user whose Employee matches (the directory's
	company and department filters; a user with no Employee matches neither)."""
	_only(USER_ADMIN)
	filters = {"user_id": ["is", "set"]}
	names = _json(users, None)
	if names is not None:
		if not isinstance(names, list) or len(names) > 500:
			frappe.throw(_("users is a list of at most 500 names."))
		filters["user_id"] = ["in", names or [""]]
	if company:
		filters["company"] = company
	if department:
		filters["department"] = department
	out = {}
	for e in frappe.get_all("Employee", filters=filters, fields=["name", "user_id", "employee_name", "company", "department", "designation"], order_by="modified desc"):
		out.setdefault(e.user_id, {"employee": e.name, "employee_name": e.employee_name, "company": e.company, "department": e.department, "designation": e.designation})
	return out


@frappe.whitelist(methods=["GET"])
def user_profile(user):
	"""What the user page shows beyond the User row: the profile fields the forms edit, the linked
	employee (with company, department, designation and manager), and whether a reset link may be
	sent (the user is enabled and has an e-mail address)."""
	_only(USER_ADMIN)
	doc, refused = _target(user)
	if refused:
		return refused
	return {
		"user": doc.name,
		"fields": {f: doc.get(f) for f in (*PROFILE_FIELDS, *SYSTEM_ONLY_FIELDS, "email")},
		"employee": _employee_of(doc.name),
		"can_reset_password": bool(doc.enabled and doc.email),
		"can_change": _ceiling(doc) is None,
		"last_password_reset": cstr(doc.last_password_reset_date) or None,
	}


# --- create --------------------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def create_user(user):
	"""A new login. `user` is {email, first_name, middle_name?, last_name?, username?, mobile_no?,
	user_type?, roles[], module_profile?, default_workspace?, default_app?, language?, time_zone?,
	employee?, send_welcome_email?}. No password is ever set here: the person sets their own from
	Frappe's welcome e-mail (send_welcome_email, default on) or from a reset link sent later."""
	_only(USER_ADMIN)
	data = _json(user, {}) or {}
	if not isinstance(data, dict):
		frappe.throw(_("user is an object."))
	email = validate_email_address(cstr(data.get("email")).strip(), True)
	if frappe.db.exists("User", email):
		return _refuse("duplicate", _("A user with {0} already exists.").format(email))
	if not cstr(data.get("first_name")).strip():
		return _refuse("invalid_user", _("A first name is required."))

	user_type = data.get("user_type") or "System User"
	if user_type not in USER_TYPES:
		return _refuse("invalid_user", _("Not a user type: {0}.").format(user_type))
	if user_type != "System User" and not _is_system_manager():
		return _refuse("privileged_field", _("Only a System Manager can create a {0}.").format(user_type))

	roles = [cstr(r) for r in (data.get("roles") or [])]
	refused = _check_new_roles(roles)
	if refused:
		return refused

	employee = cstr(data.get("employee")).strip() or None
	if employee:
		emp_user = frappe.db.get_value("Employee", employee, "user_id")
		if emp_user is None and not frappe.db.exists("Employee", employee):
			return _refuse("not_found", _("There is no employee {0}.").format(employee))
		if emp_user:
			return _refuse("employee_linked", _("{0} is already linked to {1}.").format(employee, emp_user))
	elif any(r in EMPLOYEE_ROLES for r in roles):
		return _refuse("employee_role_needs_employee", _("Choose the employee this login belongs to, or leave out {0}.").format(" / ".join(EMPLOYEE_ROLES)))

	doc = frappe.new_doc("User")
	doc.update({f: data.get(f) for f in PROFILE_FIELDS if data.get(f) not in (None, "")})
	doc.update({"email": email, "user_type": user_type, "enabled": 1, "send_welcome_email": 1 if data.get("send_welcome_email", True) else 0})
	for role in roles:
		doc.append("roles", {"role": role})
	doc.flags.ignore_permissions = True
	doc.insert()
	if employee:
		_set_employee_user(employee, doc.name)
	_record("user created", "User", doc.name, {}, {"roles": roles, "user_type": user_type, "employee": employee, "welcome_email": bool(doc.send_welcome_email)})
	return {"user": doc.name, "roles": _user_roles(doc.name), "employee": _employee_of(doc.name)}


def _check_new_roles(roles):
	if len(roles) > MAX_ROLE_CHANGES:
		return _refuse("invalid_user", _("At most {0} roles at a time.").format(MAX_ROLE_CHANGES))
	auto = [r for r in roles if r in AUTOMATIC_ROLES]
	if auto:
		return _refuse("automatic_role", _("{0} is assigned automatically and is never added or removed.").format(auto[0]))
	known = {r.name: r.disabled for r in frappe.get_all("Role", filters={"name": ["in", roles or [""]]}, fields=["name", "disabled"])}
	unknown = [r for r in roles if r not in known]
	if unknown:
		return _refuse("unknown_role", _("Not a role: {0}.").format(", ".join(unknown)))
	off = [r for r in roles if known[r]]
	if off:
		return _refuse("unknown_role", _("Disabled role: {0}.").format(", ".join(off)))
	if not _is_system_manager():
		privileged = sorted(PRIVILEGED_ROLES.intersection(roles))
		if privileged:
			return _refuse("privileged_role", _("Only a System Manager can grant or remove {0}.").format(", ".join(privileged)))
	return None


# --- edit ------------------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def update_user(user, values, modified=None):
	"""Change profile fields (PROFILE_FIELDS; user_type for a System Manager only)."""
	_only(USER_ADMIN)
	doc, refused = _target(user)
	if refused:
		return refused
	values = _json(values, {}) or {}
	if not isinstance(values, dict) or not values:
		return _refuse("invalid_user", _("Nothing to change."))
	allowed = set(PROFILE_FIELDS) | set(SYSTEM_ONLY_FIELDS)
	extra = sorted(set(values) - allowed)
	if extra:
		return _refuse("field_not_allowed", _("These cannot be changed here: {0}.").format(", ".join(extra)))
	sm = _is_system_manager()
	if not sm and set(values) & set(SYSTEM_ONLY_FIELDS):
		return _refuse("privileged_field", _("Only a System Manager can change the user type."))
	if "user_type" in values:
		if values["user_type"] not in USER_TYPES:
			return _refuse("invalid_user", _("Not a user type: {0}.").format(values["user_type"]))
		if _same(doc.name, frappe.session.user):
			return _refuse("self_user_type", _("You cannot change your own user type."))
	refused = _ceiling(doc) or _stale(doc, modified)
	if refused:
		return refused
	before = {f: doc.get(f) for f in values}
	changed = {f: v for f, v in values.items() if cstr(doc.get(f)) != cstr(v)}
	if changed:
		doc.update(changed)
		doc.flags.ignore_permissions = True
		doc.save()
		_record("user edited", "User", doc.name, before, {f: doc.get(f) for f in values})
	return {"user": doc.name, "modified": cstr(doc.modified)}


@frappe.whitelist(methods=["POST"])
def set_enabled(user, enabled, modified=None):
	_only(USER_ADMIN)
	doc, refused = _target(user)
	if refused:
		return refused
	want = cint(enabled)
	if _same(doc.name, frappe.session.user):
		return _refuse("self_disable", _("You cannot disable or enable your own user."))
	refused = _ceiling(doc) or _stale(doc, modified)
	if refused:
		return refused
	if cint(doc.enabled) == want:
		return {"user": doc.name, "changed": False}
	if not want and SYSTEM_MANAGER in {r.role for r in doc.roles} and not other_system_managers(doc.name):
		return _refuse("last_system_manager", _("This is the last enabled System Manager; another user must hold the role first."))
	doc.enabled = want
	doc.flags.ignore_permissions = True
	doc.save()
	_record("user enabled" if want else "user disabled", "User", doc.name, {"enabled": 1 - want}, {"enabled": want})
	return {"user": doc.name, "changed": True}


@frappe.whitelist(methods=["POST"])
def change_roles(user, add=None, remove=None, modified=None):
	"""Add and remove roles. Refuses -- with the reason -- any change the ceiling forbids or the
	ERP would quietly undo on save (role profiles rebuild roles; erpnext strips Employee from a
	user no Employee links to; hrms re-adds Leave/Expense Approver to an approver; erpnext re-adds
	Supplier/Customer to a linked contact). Answers with the roles before and after the save: the
	ERP may still add or strip a role on its own, and the BFF reports that as a notice."""
	_only(USER_ADMIN)
	add, remove = _dedupe(_json(add, []) or []), _dedupe(_json(remove, []) or [])
	if len(add) > MAX_ROLE_CHANGES or len(remove) > MAX_ROLE_CHANGES:
		return _refuse("invalid_user", _("add and remove may name at most {0} roles each.").format(MAX_ROLE_CHANGES))
	both = set(add) & set(remove)
	if both:
		return _refuse("invalid_user", _("{0} is in both add and remove.").format(sorted(both)[0]))
	auto = [r for r in (*add, *remove) if r in AUTOMATIC_ROLES]
	if auto:
		return _refuse("automatic_role", _("{0} is assigned automatically and is never added or removed.").format(auto[0]))
	known = {r.name: r.disabled for r in frappe.get_all("Role", filters={"name": ["in", [*add, *remove] or [""]]}, fields=["name", "disabled"])}
	unknown = [r for r in (*add, *remove) if r not in known]
	if unknown:
		return _refuse("unknown_role", _("Not a role: {0}.").format(", ".join(unknown)))
	off = [r for r in add if known[r]]
	if off:
		return _refuse("unknown_role", _("Disabled role: {0}.").format(", ".join(off)))

	doc, refused = _target(user)
	if refused:
		return refused
	sm, me = _is_system_manager(), _same(doc.name, frappe.session.user)
	if me and SYSTEM_MANAGER in remove:
		return _refuse("self_system_manager", _("You cannot remove System Manager from yourself."))
	if me and not sm and (add or remove):
		return _refuse("self_roles", _("You cannot change your own roles; ask another administrator."))
	if not sm:
		privileged = sorted(PRIVILEGED_ROLES.intersection((*add, *remove)))
		if privileged:
			return _refuse("privileged_role", _("Only a System Manager can grant or remove {0}.").format(", ".join(privileged)))
	refused = _ceiling(doc) or _stale(doc, modified)
	if refused:
		return refused

	if doc.get("role_profiles") and (add or remove):
		# User.populate_role_profile_roles would rebuild the roles from the profiles on save.
		return _refuse("role_profile_managed", _("This user's roles come from a role profile; change the profile instead."))
	current = [r.role for r in doc.roles]
	adding = [r for r in add if r not in current]
	removing = [r for r in remove if r in current]
	if not adding and not removing:
		return {"user": doc.name, "before": current, "after": current, "changed": False}
	if SYSTEM_MANAGER in removing and doc.enabled and not other_system_managers(doc.name):
		return _refuse("last_system_manager", _("This is the last enabled System Manager; another user must hold the role first."))
	refused = _employee_guard(doc, adding, removing) or _portal_guard(doc, removing)
	if refused:
		return refused

	doc.set("roles", [r for r in doc.roles if r.role not in removing])
	for role in adding:
		doc.append("roles", {"role": role})
	doc.flags.ignore_permissions = True
	doc.save()
	after = _user_roles(doc.name)
	_record("roles changed", "User", doc.name, {"roles": current}, {"roles": after})
	return {"user": doc.name, "before": current, "after": after, "changed": True}


def _dedupe(xs):
	out = []
	for x in xs if isinstance(xs, list) else []:
		x = cstr(x)
		if x not in out:
			out.append(x)
	return out


def _employee_guard(doc, adding, removing):
	if any(r in EMPLOYEE_ROLES for r in adding) and not frappe.db.exists("Employee", {"user_id": doc.name}):
		return _refuse("employee_role_needs_employee", _("No Employee record is linked to this user, so the ERP would remove that role again. Link an Employee first."))
	for role in removing:
		field = APPROVER_FIELDS.get(role)
		if field and frappe.db.exists("Employee", {field: doc.name}):
			return _refuse("approver_role_assigned", _("This user is still an Employee's approver, so the ERP would add {0} back. Reassign the approver first.").format(role))
	return None


def _portal_guard(doc, removing):
	portal = [r for r in removing if r in PORTAL_ROLES]
	if not portal:
		return None
	contacts = frappe.get_all("Contact", filters={"email_id": doc.email or doc.name}, pluck="name")
	if not contacts:
		return None
	linked = set(
		frappe.get_all("Dynamic Link", filters={"parenttype": "Contact", "parent": ["in", contacts], "link_doctype": ["in", list(PORTAL_ROLES)]}, pluck="link_doctype")
	)
	for role in portal:
		if role in linked:
			return _refuse("portal_role_linked", _("This user's Contact is linked to a {0}, so the ERP would add the role back. Unlink the Contact first.").format(role))
	return None


# --- password, employee, delete -------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def send_password_reset(user):
	"""Frappe's own reset e-mail (a link to set a new password). The link is never shown here."""
	_only(USER_ADMIN)
	doc, refused = _target(user)
	if refused:
		return refused
	refused = _ceiling(doc)
	if refused:
		return refused
	if not doc.enabled:
		return _refuse("user_disabled", _("{0} is disabled; enable the user first.").format(doc.name))
	if not doc.email:
		return _refuse("no_email", _("{0} has no e-mail address to send the link to.").format(doc.name))
	doc._reset_password(send_email=True)
	_record("password reset link sent", "User", doc.name)
	return {"user": doc.name, "sent": True}


@frappe.whitelist(methods=["POST"])
def link_employee(user, employee):
	"""Make `employee` this user's Employee record (Employee.user_id). ERPNext then gives the
	login the Employee role on its next save; HR's own link (hr_portal) is the same move."""
	_only(USER_ADMIN)
	doc, refused = _target(user)
	if refused:
		return refused
	refused = _ceiling(doc)
	if refused:
		return refused
	emp = frappe.db.get_value("Employee", cstr(employee), ["name", "user_id"], as_dict=True)
	if not emp:
		return _refuse("not_found", _("There is no employee {0}.").format(employee))
	if emp.user_id:
		return _refuse("employee_linked", _("{0} is already linked to {1}.").format(emp.name, emp.user_id))
	current = frappe.db.get_value("Employee", {"user_id": doc.name}, "name")
	if current:
		return _refuse("employee_linked", _("{0} is already linked to {1}; unlink it first.").format(doc.name, current))
	_set_employee_user(emp.name, doc.name)
	_record("employee linked", "User", doc.name, {"employee": None}, {"employee": emp.name})
	return {"user": doc.name, "employee": _employee_of(doc.name)}


@frappe.whitelist(methods=["POST"])
def unlink_employee(user):
	_only(USER_ADMIN)
	doc, refused = _target(user)
	if refused:
		return refused
	refused = _ceiling(doc)
	if refused:
		return refused
	current = frappe.db.get_value("Employee", {"user_id": doc.name}, "name")
	if not current:
		return {"user": doc.name, "employee": None}
	_set_employee_user(current, None)
	_record("employee unlinked", "User", doc.name, {"employee": current}, {"employee": None})
	return {"user": doc.name, "employee": None}


def _set_employee_user(employee, user):
	"""As hr_portal._link, with create_user_permission off (a User Permission pinning the user to
	the record is not what FoLT relies on), saved past Employee permissions: the caller passed the
	administrator floor, and a User Administrator holds no Employee write."""
	emp = frappe.get_doc("Employee", employee)
	emp.user_id = user
	emp.create_user_permission = 0
	emp.flags.ignore_permissions = True
	emp.save()


@frappe.whitelist(methods=["POST"])
def delete_user(user):
	"""Delete a login that has no history: never signed in, no Employee, nothing it created or
	changed. Anyone with history is disabled instead -- deleting them would orphan the audit
	trail -- and the answer says so."""
	_only(USER_ADMIN)
	doc, refused = _target(user)
	if refused:
		return refused
	if _same(doc.name, frappe.session.user):
		return _refuse("self_delete", _("You cannot delete your own user."))
	refused = _ceiling(doc)
	if refused:
		return refused
	history = (
		bool(doc.last_login)
		or frappe.db.exists("Employee", {"user_id": doc.name})
		or frappe.db.exists("Version", {"owner": doc.name})
		or frappe.db.exists("Activity Log", {"user": doc.name, "operation": ["in", ["Login", "Logout"]]})
	)
	if history:
		return _refuse("has_history", _("{0} has signed in or changed records, so the user is kept for the audit trail. Disable it instead.").format(doc.name))
	roles = [r.role for r in doc.roles]
	_delete_user(doc.name)
	_record("user deleted", "User", doc.name, {"roles": roles}, {})
	return {"user": doc.name, "deleted": True}


def _delete_user(name, attempts=3):
	"""frappe.delete_doc, retried on a snapshot conflict. Saving a User makes Frappe sync its Contact
	in a background job, so a delete made right after an edit can find that Contact changed since
	this transaction first read it (MariaDB 1020, QueryDeadlockError). Nothing has been written yet
	when it happens -- the checks above only read -- so a rollback and a fresh snapshot are safe."""
	import time

	for attempt in range(attempts):
		try:
			frappe.delete_doc("User", name, ignore_permissions=True)
			return
		except frappe.QueryDeadlockError:
			if attempt == attempts - 1:
				raise
			frappe.db.rollback()
			time.sleep(0.3 * (attempt + 1))


# --- history -------------------------------------------------------------------------------


@frappe.whitelist(methods=["GET"])
def user_activity(user, kind="logins", start=0, limit=20):
	"""A user's sign-ins (Activity Log: login, logout, impersonation, failures) or the records they
	changed (Version: doctype, name and when -- never the values)."""
	_only(USER_ADMIN)
	doc, refused = _target(user)
	if refused:
		return refused
	start, limit = max(cint(start), 0), min(max(cint(limit), 1), 100)
	if kind == "logins":
		rows = frappe.get_all(
			"Activity Log",
			filters={"user": doc.name, "operation": ["in", ["Login", "Logout", "Impersonate"]]},
			fields=["name", "creation", "operation", "status", "ip_address", "subject"],
			order_by="creation desc",
			start=start,
			limit=limit + 1,
		)
		items = [{"at": cstr(r.creation), "what": r.operation, "status": r.status, "ip": r.ip_address, "detail": frappe.utils.strip_html(r.subject or "")} for r in rows[:limit]]
	elif kind == "changes":
		rows = frappe.get_all(
			"Version",
			filters={"owner": doc.name},
			fields=["name", "creation", "ref_doctype", "docname"],
			order_by="creation desc",
			start=start,
			limit=limit + 1,
		)
		items = [{"at": cstr(r.creation), "what": r.ref_doctype, "status": None, "ip": None, "detail": r.docname} for r in rows[:limit]]
	else:
		return _refuse("invalid_user", _("kind is logins or changes."))
	return {"user": doc.name, "kind": kind, "items": items, "more": len(rows) > limit}
