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


# =================================================================================================
# Phase 2: roles, role profiles, the permission matrix, user permissions, Desk access, sessions.
# =================================================================================================

ROLE_FLAGS = ("desk_access", "two_factor_auth", "disabled")


def _role_view(name):
	r = frappe.db.get_value("Role", name, ["name", "desk_access", "two_factor_auth", "disabled", "is_custom", "creation", "modified"], as_dict=True)
	users = frappe.db.sql(
		"""select u.name, u.full_name, u.enabled from `tabHas Role` r join `tabUser` u on u.name = r.parent
		where r.parenttype = 'User' and r.role = %s and u.name not in ('Administrator', 'Guest') order by u.enabled desc, u.full_name""",
		name,
		as_dict=True,
	)
	perms = {}
	for p in frappe.get_all("Custom DocPerm", filters={"role": name, "permlevel": 0}, fields=["parent", "read", "write", "create", "submit", "delete"]):
		perms[p.parent] = p
	for p in frappe.get_all("DocPerm", filters={"role": name, "permlevel": 0}, fields=["parent", "read", "write", "create", "submit", "delete"]):
		# DocPerm counts only where the doctype has no Custom DocPerm (which then replaces it).
		if p.parent not in perms and not frappe.db.exists("Custom DocPerm", {"parent": p.parent}):
			perms[p.parent] = p
	summary = [
		{"doctype": d, "read": bool(p.read), "write": bool(p.write), "create": bool(p.create), "submit": bool(p.submit), "delete": bool(p.delete)}
		for d, p in sorted(perms.items())
	]
	return {
		"name": r.name,
		"desk_access": bool(r.desk_access),
		"two_factor_auth": bool(r.two_factor_auth),
		"disabled": bool(r.disabled),
		"custom": bool(r.is_custom),
		"privileged": r.name in PRIVILEGED_ROLES,
		"automatic": r.name in AUTOMATIC_ROLES,
		"editable": bool(r.is_custom) and r.name not in PRIVILEGED_ROLES and _is_system_manager(),
		"created": cstr(r.creation),
		"modified": cstr(r.modified),
		"users": [{"user": u.name, "full_name": u.full_name, "enabled": bool(u.enabled)} for u in users],
		"permissions": summary,
	}


@frappe.whitelist(methods=["GET"])
def role_detail(role):
	_only(USER_ADMIN)
	name = frappe.db.get_value("Role", cstr(role).strip(), "name")
	if not name:
		return _refuse("not_found", _("There is no role {0}.").format(role))
	return _role_view(name)


@frappe.whitelist(methods=["POST"])
def create_role(role):
	"""A custom role: name, desk access, two-factor. It grants nothing until permissions are given."""
	_only()
	data = _json(role, {}) or {}
	name = cstr(data.get("role_name")).strip()
	if not name or len(name) > 140:
		return _refuse("invalid_role", _("A role needs a name of at most 140 characters."))
	if frappe.db.exists("Role", name):
		return _refuse("duplicate", _("There is already a role {0}.").format(name))
	doc = frappe.get_doc(
		{
			"doctype": "Role",
			"role_name": name,
			"is_custom": 1,
			"desk_access": 1 if data.get("desk_access", True) else 0,
			"two_factor_auth": 1 if data.get("two_factor_auth") else 0,
		}
	).insert(ignore_permissions=True)
	_record("role created", "Role", doc.name, {}, {f: doc.get(f) for f in ROLE_FLAGS})
	return _role_view(doc.name)


@frappe.whitelist(methods=["POST"])
def update_role(role, values, modified=None):
	"""Desk access, two-factor and disabled, on a custom role that is not privileged. Standard roles
	belong to their apps (a migrate would put them back); privileged ones are the ceiling's."""
	_only()
	name = frappe.db.get_value("Role", cstr(role).strip(), "name")
	if not name:
		return _refuse("not_found", _("There is no role {0}.").format(role))
	doc = frappe.get_doc("Role", name)
	if not doc.is_custom or name in PRIVILEGED_ROLES or name in AUTOMATIC_ROLES:
		return _refuse("standard_role", _("{0} is not a custom role, so it is not changed here.").format(name))
	values = _json(values, {}) or {}
	extra = sorted(set(values) - set(ROLE_FLAGS))
	if not values or extra:
		return _refuse("field_not_allowed", _("Only desk access, two-factor and disabled are changed here."))
	if modified and cstr(modified) != cstr(doc.modified):
		return _refuse("stale", _("Someone else changed this role since you opened it. Reload and try again."))
	before = {f: doc.get(f) for f in values}
	doc.update({f: 1 if v else 0 for f, v in values.items()})
	doc.flags.ignore_permissions = True
	doc.save()
	_record("role edited", "Role", name, before, {f: doc.get(f) for f in values})
	return _role_view(name)


# --- role profiles ------------------------------------------------------------------------------


def _profile_view(name):
	roles = frappe.get_all("Has Role", filters={"parenttype": "Role Profile", "parent": name}, pluck="role", order_by="role asc")
	users = frappe.get_all("User Role Profile", filters={"parenttype": "User", "role_profile": name}, pluck="parent")
	return {"name": name, "roles": roles, "users": sorted(set(users)) if users else [], "privileged": bool(PRIVILEGED_ROLES.intersection(roles))}


@frappe.whitelist(methods=["GET"])
def role_profiles():
	_only(USER_ADMIN)
	return {"profiles": [_profile_view(n) for n in frappe.get_all("Role Profile", pluck="name", order_by="name asc")]}


@frappe.whitelist(methods=["POST"])
def save_role_profile(name, roles, new=0):
	"""Create a role profile or replace its roles. Frappe then rebuilds the roles of every user who
	holds the profile (RoleProfile.on_update), so this is a System Manager's change."""
	_only()
	name = cstr(name).strip()
	roles = _dedupe(_json(roles, []) or [])
	if not name or len(name) > 140:
		return _refuse("invalid_role", _("A role profile needs a name of at most 140 characters."))
	refused = _check_new_roles(roles)
	if refused:
		return refused
	exists = frappe.db.exists("Role Profile", name)
	if cint(new) and exists:
		return _refuse("duplicate", _("There is already a role profile {0}.").format(name))
	if not cint(new) and not exists:
		return _refuse("not_found", _("There is no role profile {0}.").format(name))
	before = _profile_view(name)["roles"] if exists else []
	doc = frappe.get_doc("Role Profile", name) if exists else frappe.new_doc("Role Profile")
	if not exists:
		doc.role_profile = name
	doc.set("roles", [{"role": r} for r in roles])
	doc.flags.ignore_permissions = True
	doc.save()
	_record("role profile saved", "Role Profile", doc.name, {"roles": before}, {"roles": roles})
	return _profile_view(doc.name)


@frappe.whitelist(methods=["POST"])
def set_user_role_profiles(user, profiles, modified=None):
	"""The role profiles a user holds; Frappe rebuilds their roles from them on save. A delegated
	administrator may give only profiles without privileged roles, to users below the ceiling."""
	_only(USER_ADMIN)
	doc, refused = _target(user)
	if refused:
		return refused
	profiles = _dedupe(_json(profiles, []) or [])
	unknown = [p for p in profiles if not frappe.db.exists("Role Profile", p)]
	if unknown:
		return _refuse("not_found", _("No role profile {0}.").format(", ".join(unknown)))
	sm = _is_system_manager()
	if not sm:
		if _same(doc.name, frappe.session.user):
			return _refuse("self_roles", _("You cannot change your own roles; ask another administrator."))
		if any(_profile_view(p)["privileged"] for p in profiles):
			return _refuse("privileged_role", _("Only a System Manager can give a profile with a privileged role."))
	refused = _ceiling(doc) or _stale(doc, modified)
	if refused:
		return refused
	before = [r.role_profile for r in doc.get("role_profiles") or []]
	if before == profiles:
		return {"user": doc.name, "changed": False}
	doc.set("role_profiles", [{"role_profile": p} for p in profiles])
	doc.flags.ignore_permissions = True
	doc.save()
	_record("role profiles changed", "User", doc.name, {"role_profiles": before}, {"role_profiles": profiles})
	return {"user": doc.name, "changed": True}


# --- the permission matrix -------------------------------------------------------------------------

PTYPES = ("select", "read", "write", "create", "delete", "submit", "cancel", "amend", "print", "email", "report", "import", "export", "share", "mask", "impersonate")
SUBMIT_PTYPES = ("submit", "cancel", "amend")
# Write-like rights: on these doctypes they decide who administers the site, so only System
# Manager holds them; granting one to another role through the matrix would step past the ceiling.
SECURITY_DOCTYPES = (
	"User", "Role", "Role Profile", "Module Profile", "Has Role", "Custom DocPerm", "DocPerm", "DocType", "User Permission",
	"System Settings", "Custom Field", "Property Setter", "Server Script", "Client Script", "Workflow", "OAuth Client",
	"Webhook", "Email Account", "Workspace",
)
WRITE_PTYPES = ("write", "create", "delete", "submit", "cancel", "amend", "import", "share", "impersonate")
BROAD_ROLES = ("Guest", "All", "Desk User")


def _readers(doctype, permlevel=0):
	return {p.role for p in frappe.get_meta(doctype).permissions if cint(p.permlevel) == permlevel and p.read}


@frappe.whitelist(methods=["GET"])
def permission_sources(doctypes):
	"""Which ticks FoLT's code grants (re-applied on every migrate) and which an administrator set
	here (kept by the overrides store): {doctype: [{role, permlevel, ptype, source}]}."""
	_only()
	from folt_customizations import permissions as perms

	names = _json(doctypes, []) or []
	if not isinstance(names, list) or len(names) > 50:
		frappe.throw(_("doctypes is a list of at most 50 names."))
	store = perms.permission_overrides()
	out = {}
	for dt in names:
		rows = []
		for (role, level), ptypes in perms.code_grants(dt).items():
			for p in sorted(ptypes):
				if not perms.revoked(dt, role, level, p):
					rows.append({"role": role, "permlevel": level, "ptype": p, "source": "code"})
		for role, levels in store.get(dt, {}).items():
			for level, ptypes in levels.items():
				for p, v in ptypes.items():
					rows.append({"role": role, "permlevel": int(level), "ptype": p, "source": "granted" if v else "revoked"})
		out[dt] = rows
	return out


@frappe.whitelist(methods=["POST"])
def set_permission(doctype, role, permlevel, ptype, value):
	"""Tick or untick one right of one role on one doctype, as the Role Permissions Manager does,
	and record it so a migrate does not undo it."""
	_only()
	from folt_customizations import permissions as perms

	doctype, role, ptype, permlevel, value = cstr(doctype), cstr(role), cstr(ptype), cint(permlevel), cint(value)
	meta = frappe.db.get_value("DocType", doctype, ["name", "istable", "is_submittable"], as_dict=True)
	if not meta:
		return _refuse("not_found", _("There is no doctype {0}.").format(doctype))
	doctype = meta.name  # canonical: the BFF uses it as a path segment
	if meta.istable:
		return _refuse("child_table", _("{0} is a child table; it follows its parent's permissions.").format(doctype))
	if not frappe.db.exists("Role", role) or role == "Administrator":
		return _refuse("unknown_role", _("Not a role that takes permissions here: {0}.").format(role))
	if ptype not in PTYPES or not 0 <= permlevel <= 9:
		return _refuse("invalid_permission", _("Not a permission: {0} at level {1}.").format(ptype, permlevel))
	if ptype in SUBMIT_PTYPES and not meta.is_submittable:
		return _refuse("not_submittable", _("{0} is not submittable, so it has no {1}.").format(doctype, ptype))
	if value:
		if doctype in SECURITY_DOCTYPES and ptype in WRITE_PTYPES and role != SYSTEM_MANAGER:
			return _refuse("protected_permission", _("Only System Manager may hold {0} on {1}: it administers the site.").format(ptype, doctype))
		if role in BROAD_ROLES and ptype in WRITE_PTYPES:
			return _refuse("protected_permission", _("{0} is held by every login, so it is never given {1}.").format(role, ptype))
	else:
		if role == SYSTEM_MANAGER and doctype in SECURITY_DOCTYPES and ptype in ("read", "write"):
			return _refuse("protected_permission", _("System Manager keeps {0} on {1}, or nobody could administer it.").format(ptype, doctype))
		if ptype == "read" and _readers(doctype, permlevel) == {role}:
			return _refuse("last_reader", _("{0} is the only role that reads {1}; give another role read first.").format(role, doctype))

	current = next((p for p in frappe.get_meta(doctype).permissions if p.role == role and cint(p.permlevel) == permlevel and not p.if_owner), None)
	old = cint(current.get(ptype)) if current else 0
	if old == value:
		return {"doctype": doctype, "changed": False}
	if value and not current:
		perms._grant(doctype, role, [ptype], permlevel)
	else:
		update_permission_property(doctype, role, permlevel, ptype, value)
	frappe.clear_cache(doctype=doctype)
	perms.record_override(doctype, role, permlevel, ptype, value)
	_record("permission changed", "DocType", doctype, {f"{role} L{permlevel} {ptype}": old}, {f"{role} L{permlevel} {ptype}": value})
	return {"doctype": doctype, "changed": True}


def update_permission_property(doctype, role, permlevel, ptype, value):
	from frappe.permissions import update_permission_property as _update

	_update(doctype, role, permlevel, ptype, value)


@frappe.whitelist(methods=["POST"])
def reset_permissions(doctype):
	"""Back to the doctype's shipped permissions plus what FoLT's code grants; the administrator's
	recorded changes for it are dropped."""
	_only()
	from frappe.permissions import reset_perms

	from folt_customizations import permissions as perms

	name = frappe.db.get_value("DocType", cstr(doctype), "name")
	if not name:
		return _refuse("not_found", _("There is no doctype {0}.").format(doctype))
	doctype = name
	reset_perms(doctype)
	perms.clear_overrides(doctype)
	perms.apply_role_permissions()
	frappe.clear_cache(doctype=doctype)
	_record("permissions reset", "DocType", doctype)
	return {"doctype": doctype, "changed": True}


@frappe.whitelist(methods=["GET"])
def doctype_readers(doctypes):
	"""{doctype: [roles that read it]} -- what decides who sees a document's shortcut in the app."""
	_only(USER_ADMIN)
	names = _json(doctypes, []) or []
	if not isinstance(names, list) or len(names) > 50:
		frappe.throw(_("doctypes is a list of at most 50 names."))
	return {dt: sorted(_readers(dt)) for dt in names if frappe.db.exists("DocType", dt)}


# --- user permissions (record-level restrictions) --------------------------------------------------

USER_PERMISSION_DOCTYPES = ("Company", "Department", "Branch", "Cost Center", "Project")


def _user_permission_rows(user):
	return [
		{
			"name": r.name,
			"allow": r.allow,
			"for_value": r.for_value,
			"apply_to_all_doctypes": bool(r.apply_to_all_doctypes),
			"applicable_for": r.applicable_for,
			"is_default": bool(r.is_default),
		}
		for r in frappe.get_all(
			"User Permission",
			filters={"user": user},
			fields=["name", "allow", "for_value", "apply_to_all_doctypes", "applicable_for", "is_default"],
			order_by="allow asc, for_value asc",
		)
	]


@frappe.whitelist(methods=["GET"])
def user_permissions(user):
	"""The records a user is restricted to, and the choices for adding one."""
	_only(USER_ADMIN)
	doc, refused = _target(user)
	if refused:
		return refused
	return {
		"user": doc.name,
		"rows": _user_permission_rows(doc.name),
		"allow": list(USER_PERMISSION_DOCTYPES),
		"can_change": _ceiling(doc) is None,
	}


@frappe.whitelist(methods=["GET"])
def user_permission_values(allow, q=None):
	"""Records of an allowed doctype to restrict a user to (first 50 matching q)."""
	_only(USER_ADMIN)
	if allow not in USER_PERMISSION_DOCTYPES:
		return _refuse("invalid_permission", _("Users are restricted here by {0} only.").format(", ".join(USER_PERMISSION_DOCTYPES)))
	filters = {"name": ["like", f"%{cstr(q).strip()}%"]} if q else None
	return {"allow": allow, "values": frappe.get_all(allow, filters=filters, pluck="name", order_by="name asc", limit=50)}


@frappe.whitelist(methods=["POST"])
def add_user_permission(user, allow, for_value, applicable_for=None, is_default=0):
	_only(USER_ADMIN)
	doc, refused = _target(user)
	if refused:
		return refused
	refused = _ceiling(doc)
	if refused:
		return refused
	if allow not in USER_PERMISSION_DOCTYPES:
		return _refuse("invalid_permission", _("Users are restricted here by {0} only.").format(", ".join(USER_PERMISSION_DOCTYPES)))
	if not frappe.db.exists(allow, for_value):
		return _refuse("not_found", _("There is no {0} {1}.").format(allow, for_value))
	if applicable_for and not frappe.db.exists("DocType", applicable_for):
		return _refuse("not_found", _("There is no doctype {0}.").format(applicable_for))
	filters = {"user": doc.name, "allow": allow, "for_value": for_value, "applicable_for": applicable_for or ["is", "not set"]}
	if frappe.db.exists("User Permission", filters):
		return _refuse("duplicate", _("{0} is already restricted to {1} {2}.").format(doc.name, allow, for_value))
	frappe.get_doc(
		{
			"doctype": "User Permission",
			"user": doc.name,
			"allow": allow,
			"for_value": for_value,
			"apply_to_all_doctypes": 0 if applicable_for else 1,
			"applicable_for": applicable_for or None,
			"is_default": cint(is_default),
		}
	).insert(ignore_permissions=True)
	_record("user permission added", "User", doc.name, {}, {"restricted to": f"{allow} {for_value}" + (f" for {applicable_for}" if applicable_for else "")})
	return {"user": doc.name, "rows": _user_permission_rows(doc.name), "allow": list(USER_PERMISSION_DOCTYPES), "can_change": True}


@frappe.whitelist(methods=["POST"])
def remove_user_permission(name):
	_only(USER_ADMIN)
	row = frappe.db.get_value("User Permission", cstr(name), ["name", "user", "allow", "for_value"], as_dict=True)
	if not row:
		return _refuse("not_found", _("That restriction no longer exists."))
	doc, refused = _target(row.user)
	if refused:
		return refused
	refused = _ceiling(doc)
	if refused:
		return refused
	frappe.delete_doc("User Permission", row.name, ignore_permissions=True)
	_record("user permission removed", "User", doc.name, {"restricted to": f"{row.allow} {row.for_value}"}, {})
	return {"user": doc.name, "rows": _user_permission_rows(doc.name), "allow": list(USER_PERMISSION_DOCTYPES), "can_change": True}


# --- Desk access: workspaces and module profiles -----------------------------------------------------


@frappe.whitelist(methods=["GET"])
def desk_access():
	"""What the Desk shows: its workspaces (hidden or not, and to which roles) and the module
	profiles that block Desk modules per user. The app's own modules follow roles (BFF)."""
	_only()
	from folt_customizations.workspaces import hidden_workspaces

	hidden = hidden_workspaces()
	roles = {}
	for r in frappe.get_all("Has Role", filters={"parenttype": "Workspace"}, fields=["parent", "role"]):
		roles.setdefault(r.parent, []).append(r.role)
	workspaces = [
		{"name": w.name, "label": w.label or w.name, "module": w.module, "hidden": bool(w.is_hidden or not w.public) or w.name in hidden, "roles": sorted(roles.get(w.name, []))}
		for w in frappe.get_all("Workspace", filters={"for_user": ["in", ["", None]]}, fields=["name", "label", "module", "public", "is_hidden"], order_by="name asc")
	]
	profiles = []
	for name in frappe.get_all("Module Profile", pluck="name", order_by="name asc"):
		blocked = frappe.get_all("Block Module", filters={"parenttype": "Module Profile", "parent": name}, pluck="module", order_by="module asc")
		users = frappe.get_all("User", filters={"module_profile": name}, pluck="name")
		profiles.append({"name": name, "blocked": blocked, "users": users})
	modules = [{"value": m.name, "label": m.name, "app": m.app_name} for m in frappe.get_all("Module Def", fields=["name", "app_name"], order_by="app_name asc, name asc")]
	return {"workspaces": workspaces, "module_profiles": profiles, "modules": modules}


@frappe.whitelist(methods=["POST"])
def set_workspace_visibility(name, hidden):
	"""Hide a Desk workspace or show it again, as ticking Public does in the Desk:
	workspaces.record_visibility_intent records the decision where migrate keeps it and re-gates
	the module's icon."""
	_only()
	if not frappe.db.exists("Workspace", cstr(name)):
		return _refuse("not_found", _("There is no workspace {0}.").format(name))
	doc = frappe.get_doc("Workspace", name)
	if doc.for_user:
		return _refuse("not_found", _("{0} is somebody's own workspace.").format(name))
	want = 0 if cint(hidden) else 1
	if doc.public == want and bool(doc.is_hidden) == (not want):
		return {"name": doc.name, "hidden": not want, "changed": False}
	before = {"hidden": bool(doc.is_hidden or not doc.public)}
	doc.public = want
	doc.flags.ignore_permissions = True
	doc.save()
	_record("workspace hidden" if not want else "workspace shown", "Workspace", doc.name, before, {"hidden": not want})
	return {"name": doc.name, "hidden": not want, "changed": True}


@frappe.whitelist(methods=["POST"])
def save_module_profile(name, blocked, new=0):
	"""Create a module profile or replace the Desk modules it blocks."""
	_only()
	name = cstr(name).strip()
	blocked = _dedupe(_json(blocked, []) or [])
	if not name or len(name) > 140:
		return _refuse("invalid_role", _("A module profile needs a name of at most 140 characters."))
	unknown = [m for m in blocked if not frappe.db.exists("Module Def", m)]
	if unknown:
		return _refuse("not_found", _("No module {0}.").format(", ".join(unknown)))
	exists = frappe.db.exists("Module Profile", name)
	if cint(new) and exists:
		return _refuse("duplicate", _("There is already a module profile {0}.").format(name))
	if not cint(new) and not exists:
		return _refuse("not_found", _("There is no module profile {0}.").format(name))
	doc = frappe.get_doc("Module Profile", name) if exists else frappe.new_doc("Module Profile")
	before = [b.module for b in doc.get("block_modules") or []]
	if not exists:
		doc.module_profile_name = name
	doc.set("block_modules", [{"module": m} for m in blocked])
	doc.flags.ignore_permissions = True
	doc.save()
	_record("module profile saved", "Module Profile", doc.name, {"blocked": before}, {"blocked": blocked})
	return desk_access()


# --- sessions --------------------------------------------------------------------------------------

SESSION_READERS = (USER_ADMIN, SECURITY_ADMIN)


def _session_handle(sid):
	"""An opaque, stable handle for a session: the sid itself never leaves the server."""
	import hashlib
	import hmac

	key = (frappe.local.conf.get("encryption_key") or frappe.local.conf.get("secret_key") or frappe.local.site).encode()
	return hmac.new(key, cstr(sid).encode(), hashlib.sha256).hexdigest()[:32]


def _session_rows(user=None):
	filters = "where user not in ('Guest')" + (" and user = %(user)s" if user else "")
	rows = frappe.db.sql(f"select user, sid, ipaddress, lastupdate, sessiondata from `tabSessions` {filters} order by lastupdate desc", {"user": user}, as_dict=True)
	names = {u.name: u.full_name for u in frappe.get_all("User", filters={"name": ["in", list({r.user for r in rows}) or [""]]}, fields=["name", "full_name"])}
	out = []
	for r in rows:
		try:
			data = frappe.parse_json(r.sessiondata or "{}") or {}
		except Exception:  # noqa: BLE001 -- a malformed row still lists
			data = {}
		out.append(
			{
				"handle": _session_handle(r.sid),
				"user": r.user,
				"full_name": names.get(r.user),
				"ip": r.ipaddress or data.get("session_ip"),
				"started": cstr(data.get("creation")) or None,
				"last_active": cstr(r.lastupdate) or None,
				"agent": data.get("user_agent"),
				"current": r.sid == frappe.session.sid,
				"_sid": r.sid,
			}
		)
	return out


def _public(rows):
	return [{k: v for k, v in r.items() if k != "_sid"} for r in rows]


@frappe.whitelist(methods=["GET"])
def sessions(user=None):
	"""Signed-in sessions, newest activity first: who, from which IP and client, since when. The
	session id and its CSRF token are never returned -- each row has an opaque handle instead."""
	_only(*SESSION_READERS)
	if user:
		doc, refused = _target(user)
		if refused:
			return refused
		user = doc.name
	return {"sessions": _public(_session_rows(user))}


@frappe.whitelist(methods=["POST"])
def revoke_sessions(handle=None, user=None):
	"""Sign out one session (by handle) or every session of a user. Never your own current session
	-- that is what Sign out is for -- and, for anyone but a System Manager, never a privileged user's."""
	_only(*SESSION_READERS)
	from frappe.sessions import delete_session

	if bool(handle) == bool(user):
		return _refuse("invalid_session", _("Name one session or one user."))
	if handle:
		rows = [r for r in _session_rows() if r["handle"] == cstr(handle)]
		if not rows:
			return _refuse("not_found", _("That session has already ended."))
		if rows[0]["current"]:
			return _refuse("own_session", _("This is your own session; sign out instead."))
		target = rows[0]["user"]
	else:
		doc, refused = _target(user)
		if refused:
			return refused
		target = doc.name
		rows = [r for r in _session_rows(target) if not r["current"]]
	if target in STANDARD_USERS:
		return _refuse("protected_user", _("Administrator and Guest are never changed here."))
	doc = frappe.get_doc("User", target)
	refused = _ceiling(doc)
	if refused:
		return refused
	for r in rows:
		delete_session(r["_sid"], target, reason="Signed out by an administrator")
	_record("sessions revoked", "User", target, {}, {"sessions signed out": len(rows)})
	return {"user": target, "revoked": len(rows), "sessions": _public(_session_rows())}


# =================================================================================================
# Phase 3: System Settings, per-role and per-user security, the organisation, my preferences.
# =================================================================================================

SECURITY_ADMINS = (SYSTEM_MANAGER, SECURITY_ADMIN)

# System Settings the app changes, by who may change them. Security settings are the Security
# Administrator's too; the rest (regional, site-wide behaviour) a System Manager's alone.
# kind: bool | int | str; for str an optional set of allowed values.
SECURITY_SETTINGS = {
	"enable_two_factor_auth": ("bool", None),
	"two_factor_method": ("str", ("OTP App", "SMS", "Email")),
	"lifespan_qrcode_image": ("int", None),
	"otp_issuer_name": ("str", None),
	"bypass_2fa_for_retricted_ip_users": ("bool", None),
	"bypass_restrict_ip_check_if_2fa_enabled": ("bool", None),
	"allow_consecutive_login_attempts": ("int", None),
	"allow_login_after_fail": ("int", None),
	"session_expiry": ("str", None),
	"deny_multiple_sessions": ("bool", None),
	"login_with_email_link": ("bool", None),
	"login_with_email_link_expiry": ("int", None),
	"rate_limit_email_link_login": ("int", None),
	"allow_login_using_user_name": ("bool", None),
	"allow_login_using_mobile_number": ("bool", None),
	"enable_password_policy": ("bool", None),
	"minimum_password_score": ("str", ("1", "2", "3", "4")),
	"password_reset_limit": ("int", None),
	"force_user_to_reset_password": ("int", None),
	"logout_on_password_reset": ("bool", None),
	"reset_password_link_expiry_duration": ("int", None),
}
SYSTEM_SETTINGS_FIELDS = {
	"disable_user_pass_login": ("bool", None),
	"apply_strict_user_permissions": ("bool", None),
	"backup_limit": ("int", None),
	"country": ("link", "Country"),
	"language": ("link", "Language"),
	"currency": ("link", "Currency"),
	"time_zone": ("str", None),
	"date_format": ("select", None),
	"time_format": ("select", None),
	"number_format": ("select", None),
	"first_day_of_the_week": ("select", None),
	"float_precision": ("select", None),
	"currency_precision": ("select", None),
}


def _select_options(fieldname):
	df = frappe.get_meta("System Settings").get_field(fieldname)
	return [o for o in (df.options or "").split("\n")] if df else []


@frappe.whitelist(methods=["GET"])
def settings_meta():
	"""System Settings' fields as the settings screen needs them (labels, types, options). Meta,
	not values: nothing here is secret, and the Security Administrator has no DocType read."""
	_only(SECURITY_ADMIN)
	return [
		{"fieldname": f.fieldname, "label": f.label, "fieldtype": f.fieldtype, "options": f.options, "description": f.description}
		for f in frappe.get_meta("System Settings").fields
		if f.fieldname
	]


@frappe.whitelist(methods=["POST"])
def update_settings(values, modified=None):
	"""Change System Settings fields from the allow-lists above; Frappe's own validation runs on
	save. A Security Administrator may change only SECURITY_SETTINGS."""
	_only(SECURITY_ADMIN)
	values = _json(values, {}) or {}
	if not isinstance(values, dict) or not values:
		return _refuse("invalid_setting", _("Nothing to change."))
	sm = _is_system_manager()
	allowed = dict(SECURITY_SETTINGS, **(SYSTEM_SETTINGS_FIELDS if sm else {}))
	extra = sorted(set(values) - set(allowed))
	if extra:
		if not sm and set(extra) <= set(SYSTEM_SETTINGS_FIELDS):
			return _refuse("privileged_field", _("Only a System Manager changes {0}.").format(", ".join(extra)))
		return _refuse("setting_not_allowed", _("These cannot be changed here: {0}.").format(", ".join(extra)))
	doc = frappe.get_doc("System Settings")
	if modified and cstr(modified) != cstr(doc.modified):
		return _refuse("stale", _("Someone else changed the settings since you opened them. Reload and try again."))
	clean = {}
	for field, value in values.items():
		kind, extra_rule = allowed[field]
		if kind == "bool":
			clean[field] = 1 if value else 0
		elif kind == "int":
			if not isinstance(value, int) or isinstance(value, bool) or value < 0:
				return _refuse("invalid_setting", _("{0} is a whole number.").format(field))
			clean[field] = value
		else:
			value = cstr(value).strip()
			if kind == "link" and value and not frappe.db.exists(extra_rule, value):
				return _refuse("invalid_setting", _("There is no {0} {1}.").format(extra_rule, value))
			if kind == "select" and value not in _select_options(field):
				return _refuse("invalid_setting", _("{0} is not one of the choices for {1}.").format(value, field))
			if kind == "str" and extra_rule and value not in extra_rule:
				return _refuse("invalid_setting", _("{0} is one of {1}.").format(field, ", ".join(extra_rule)))
			if field == "time_zone":
				from frappe.core.doctype.user.user import get_timezones

				if value not in get_timezones()["timezones"]:
					return _refuse("invalid_setting", _("{0} is not a time zone.").format(value))
			if len(value) > 140:
				return _refuse("invalid_setting", _("{0} is too long.").format(field))
			clean[field] = value
	if clean.get("disable_user_pass_login") and not _other_sign_in():
		return _refuse("no_other_sign_in", _("Nobody could sign in: no social login or LDAP is set up, so passwords stay on."))
	before = {f: doc.get(f) for f in clean}
	doc.update(clean)
	doc.flags.ignore_permissions = True
	doc.save()
	_record("settings changed", "System Settings", "System Settings", before, {f: doc.get(f) for f in clean})
	return {"modified": cstr(doc.modified), "changed": sorted(clean)}


def _other_sign_in():
	return bool(
		frappe.db.exists("Social Login Key", {"enable_social_login": 1})
		or frappe.db.get_single_value("LDAP Settings", "enabled")
	)


# --- per-role two-factor ------------------------------------------------------------------------


@frappe.whitelist(methods=["GET"])
def role_two_factor():
	"""Every enabled role but Guest with whether it requires two-factor sign-in and how many enabled
	users hold it -- what the Security Administrator switches, without reading users."""
	_only(SECURITY_ADMIN)
	counts = dict(
		frappe.db.sql(
			"""select r.role, count(distinct u.name) from `tabHas Role` r join tabUser u on u.name = r.parent
			where r.parenttype = 'User' and u.enabled = 1 and u.name not in ('Administrator', 'Guest') group by r.role"""
		)
	)
	return [
		{"name": r.name, "two_factor_auth": bool(r.two_factor_auth), "users": counts.get(r.name, 0)}
		for r in frappe.get_all("Role", filters={"disabled": 0, "name": ["!=", "Guest"]}, fields=["name", "two_factor_auth"], order_by="name asc")
	]


@frappe.whitelist(methods=["POST"])
def set_role_two_factor(role, enabled):
	"""Require two-factor sign-in of everyone holding a role (Role.two_factor_auth), any role but
	Guest. It bites only while two-factor is on for the site."""
	_only(SECURITY_ADMIN)
	name = frappe.db.get_value("Role", cstr(role).strip(), "name")
	if not name:
		return _refuse("not_found", _("There is no role {0}.").format(role))
	if name == "Guest":
		return _refuse("standard_role", _("Guest signs in with nothing, so it cannot require two-factor."))
	before = cint(frappe.db.get_value("Role", name, "two_factor_auth"))
	if before != cint(enabled):
		frappe.db.set_value("Role", name, "two_factor_auth", cint(enabled))
		frappe.clear_cache()
		_record("role two-factor " + ("required" if cint(enabled) else "not required"), "Role", name, {"two_factor_auth": before}, {"two_factor_auth": cint(enabled)})
	return _role_view(name)


# --- per-user security -------------------------------------------------------------------------

USER_SECURITY_FIELDS = ("restrict_ip", "login_after", "login_before", "bypass_restrict_ip_check_if_2fa_enabled")


def _user_security_view(doc):
	from frappe.twofactor import get_default

	roles = [r.role for r in doc.roles]
	two_factor_roles = frappe.get_all("Role", filters={"name": ["in", roles + ["All"]], "two_factor_auth": 1}, pluck="name")
	return {
		"user": doc.name,
		"restrict_ip": doc.restrict_ip or None,
		"login_after": cint(doc.login_after) or None,
		"login_before": cint(doc.login_before) or None,
		"bypass_restrict_ip_check_if_2fa_enabled": bool(doc.bypass_restrict_ip_check_if_2fa_enabled),
		"two_factor_site": bool(cint(frappe.db.get_single_value("System Settings", "enable_two_factor_auth"))),
		"two_factor_roles": sorted(two_factor_roles),
		"authenticator_set": bool(get_default(doc.name + "_otpsecret")),
		"can_change": _ceiling(doc) is None and not _same(doc.name, frappe.session.user),
		"modified": cstr(doc.modified),
	}


@frappe.whitelist(methods=["GET"])
def user_security(user):
	_only(SECURITY_ADMIN)
	doc, refused = _target(user)
	if refused:
		return refused
	return _user_security_view(doc)


@frappe.whitelist(methods=["POST"])
def update_user_security(user, values, modified=None):
	"""Where (IP prefixes) and when (hours) a user may sign in. Never yourself -- a wrong address
	would lock you out with nobody watching -- and a privileged user only by a System Manager."""
	_only(SECURITY_ADMIN)
	doc, refused = _target(user)
	if refused:
		return refused
	if _same(doc.name, frappe.session.user):
		return _refuse("self_security", _("You cannot restrict your own sign-in; ask another administrator."))
	refused = _ceiling(doc) or _stale(doc, modified)
	if refused:
		return refused
	values = _json(values, {}) or {}
	extra = sorted(set(values) - set(USER_SECURITY_FIELDS))
	if not values or extra:
		return _refuse("field_not_allowed", _("Only restrict_ip, login_after, login_before and the 2FA bypass are changed here."))
	clean = {}
	for field, value in values.items():
		if field == "restrict_ip":
			ips = [p.strip() for p in cstr(value).replace("\n", ",").split(",") if p.strip()]
			if any(not all(ch.isdigit() or ch in ".:abcdefABCDEF/" for ch in p) for p in ips):
				return _refuse("invalid_setting", _("restrict_ip is a list of IP addresses or prefixes."))
			clean[field] = ",".join(ips) or None  # as User.validate_ip_addr writes it
		elif field in ("login_after", "login_before"):
			if value in (None, ""):
				clean[field] = 0
			elif not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 24:
				return _refuse("invalid_setting", _("{0} is an hour from 0 to 24.").format(field))
			else:
				clean[field] = value
		else:
			clean[field] = 1 if value else 0
	before = {f: doc.get(f) for f in clean}
	doc.update(clean)
	doc.flags.ignore_permissions = True
	doc.save()
	_record("sign-in restrictions changed", "User", doc.name, before, {f: doc.get(f) for f in clean})
	return _user_security_view(doc)


@frappe.whitelist(methods=["POST"])
def reset_user_otp(user):
	"""Forget a user's authenticator (they set one up again at next sign-in), and tell them by
	e-mail -- what Frappe's reset_otp_secret does, which only a System Manager may call."""
	_only(SECURITY_ADMIN)
	from frappe.twofactor import clear_default

	doc, refused = _target(user)
	if refused:
		return refused
	refused = _ceiling(doc)
	if refused:
		return refused
	settings = frappe.get_cached_doc("System Settings")
	if not settings.enable_two_factor_auth:
		return _refuse("two_factor_off", _("Two-factor sign-in is off for the site, so there is nothing to reset."))
	clear_default(doc.name + "_otplogin")
	clear_default(doc.name + "_otpsecret")
	issuer = settings.otp_issuer_name or "FoLT ERP"
	if doc.email:
		frappe.sendmail(
			recipients=doc.email,
			subject=_("OTP Secret Reset - {0}").format(issuer),
			message=_("<p>Your authenticator for {0} has been reset by an administrator. You will set it up again at your next sign-in. If you did not ask for this, tell your administrator now.</p>").format(issuer),
			delayed=False,
		)
	_record("authenticator reset", "User", doc.name)
	return _user_security_view(doc)


# --- the organisation ---------------------------------------------------------------------------

COMPANY_FIELDS = ("tax_id", "country", "phone_no", "fax", "email", "website", "date_of_establishment", "registration_details", "default_currency")
ORG_DOCTYPES = ("Department", "Designation", "Branch")


def _company_has_transactions(company):
	return bool(frappe.db.exists("GL Entry", {"company": company}))


def _organisation():
	employees = lambda field: dict(frappe.db.sql(f"select `{field}`, count(*) from tabEmployee where `{field}` is not null group by `{field}`"))  # noqa: E731
	by_dept, by_desig, by_branch = employees("department"), employees("designation"), employees("branch")
	companies = []
	for c in frappe.get_all("Company", fields=["name", "company_name", "abbr", "country", "default_currency", "tax_id", "phone_no", "fax", "email", "website", "date_of_establishment", "registration_details", "modified"], order_by="name asc"):
		c = dict(c)
		c["date_of_establishment"] = cstr(c["date_of_establishment"]) or None
		c["modified"] = cstr(c["modified"])
		c["currency_locked"] = _company_has_transactions(c["name"])
		companies.append(c)
	years = []
	for y in frappe.get_all("Fiscal Year", fields=["name", "year_start_date", "year_end_date", "disabled"], order_by="year_start_date desc"):
		years.append(
			{
				"name": y.name,
				"year_start_date": cstr(y.year_start_date),
				"year_end_date": cstr(y.year_end_date),
				"disabled": bool(y.disabled),
				"companies": frappe.get_all("Fiscal Year Company", filters={"parent": y.name}, pluck="company"),
			}
		)
	return {
		"companies": companies,
		"departments": [
			{"name": d.name, "label": d.department_name, "parent": d.parent_department, "company": d.company, "is_group": bool(d.is_group), "disabled": bool(d.disabled), "employees": by_dept.get(d.name, 0)}
			for d in frappe.get_all("Department", fields=["name", "department_name", "parent_department", "company", "is_group", "disabled"], order_by="lft asc")
		],
		"designations": [
			{"name": d.name, "description": d.description, "employees": by_desig.get(d.name, 0)}
			for d in frappe.get_all("Designation", fields=["name", "description"], order_by="name asc")
		],
		"branches": [{"name": b, "employees": by_branch.get(b, 0)} for b in frappe.get_all("Branch", pluck="name", order_by="name asc")],
		"fiscal_years": years,
		"countries": frappe.get_all("Country", pluck="name", order_by="name asc"),
		"currencies": frappe.get_all("Currency", filters={"enabled": 1}, pluck="name", order_by="name asc"),
	}


@frappe.whitelist(methods=["GET"])
def organisation():
	"""Companies, departments, designations, branches and fiscal years, with how many employees
	use each record (a record in use is not deleted)."""
	_only()
	return _organisation()


@frappe.whitelist(methods=["POST"])
def update_company(company, values, modified=None):
	"""Contact, tax and registration details of a company. Its name and abbreviation stay (they
	are in every account's name); its currency stays once it has ledger entries."""
	_only()
	name = frappe.db.get_value("Company", cstr(company), "name")
	if not name:
		return _refuse("not_found", _("There is no company {0}.").format(company))
	doc = frappe.get_doc("Company", name)
	if modified and cstr(modified) != cstr(doc.modified):
		return _refuse("stale", _("Someone else changed this company since you opened it. Reload and try again."))
	values = _json(values, {}) or {}
	extra = sorted(set(values) - set(COMPANY_FIELDS))
	if not values or extra:
		return _refuse("field_not_allowed", _("These cannot be changed here: {0}.").format(", ".join(extra) or _("nothing")))
	if "default_currency" in values and values["default_currency"] != doc.default_currency and _company_has_transactions(name):
		return _refuse("currency_locked", _("{0} already has ledger entries in {1}, so its currency stays.").format(name, doc.default_currency))
	before = {f: doc.get(f) for f in values}
	doc.update({f: (v if v not in ("",) else None) for f, v in values.items()})
	doc.flags.ignore_permissions = True
	doc.save()
	_record("company edited", "Company", name, before, {f: doc.get(f) for f in values})
	return _organisation()


@frappe.whitelist(methods=["POST"])
def save_org_record(doctype, values, name=None):
	"""Create a department, designation or branch, or change one. A department's name is fixed
	once made (Frappe names it "<name> - <abbr>"); its parent, group flag and disabled change."""
	_only()
	if doctype not in ORG_DOCTYPES:
		return _refuse("invalid_setting", _("Only {0} are kept here.").format(", ".join(ORG_DOCTYPES)))
	values = _json(values, {}) or {}
	allowed = {
		"Department": ("department_name", "parent_department", "company", "is_group", "disabled"),
		"Designation": ("designation_name", "description"),
		"Branch": ("branch",),
	}[doctype]
	extra = sorted(set(values) - set(allowed))
	if extra:
		return _refuse("field_not_allowed", _("These cannot be set here: {0}.").format(", ".join(extra)))
	if name:
		if not frappe.db.exists(doctype, name):
			return _refuse("not_found", _("There is no {0} {1}.").format(doctype, name))
		doc = frappe.get_doc(doctype, name)
		fixed = {"Department": ("department_name", "company"), "Designation": ("designation_name",), "Branch": ("branch",)}[doctype]
		if any(f in values and cstr(values[f]) != cstr(doc.get(f)) for f in fixed):
			return _refuse("field_not_allowed", _("A {0}'s name is fixed once made.").format(doctype.lower()))
		before = {f: doc.get(f) for f in values}
	else:
		key = allowed[0]
		if not cstr(values.get(key)).strip():
			return _refuse("invalid_setting", _("A name is required."))
		if doctype != "Department" and frappe.db.exists(doctype, cstr(values[key]).strip()):
			return _refuse("duplicate", _("There is already a {0} {1}.").format(doctype.lower(), values[key]))
		doc = frappe.new_doc(doctype)
		before = {}
	for f, v in values.items():
		doc.set(f, (1 if v else 0) if f in ("is_group", "disabled") else (cstr(v).strip() or None))
	doc.flags.ignore_permissions = True
	doc.save()
	_record(f"{doctype.lower()} saved", doctype, doc.name, before, {f: doc.get(f) for f in values})
	return _organisation()


@frappe.whitelist(methods=["POST"])
def delete_org_record(doctype, name):
	"""Delete a department, designation or branch nobody uses; anything in use is kept."""
	_only()
	if doctype not in ORG_DOCTYPES or not frappe.db.exists(doctype, cstr(name)):
		return _refuse("not_found", _("There is no {0} {1}.").format(doctype, name))
	field = {"Department": "department", "Designation": "designation", "Branch": "branch"}[doctype]
	if frappe.db.exists("Employee", {field: name}) or (doctype == "Department" and frappe.db.exists("Department", {"parent_department": name})):
		return _refuse("in_use", _("{0} is in use (employees or sub-departments), so it is kept. Disable it instead, where that applies.").format(name))
	try:
		frappe.db.savepoint("org_delete")
		frappe.delete_doc(doctype, name, ignore_permissions=True)
	except frappe.LinkExistsError:
		frappe.db.rollback(save_point="org_delete")
		frappe.clear_messages()
		return _refuse("in_use", _("{0} is linked from other records, so it is kept.").format(name))
	_record(f"{doctype.lower()} deleted", None, None, {"deleted": f"{doctype} {name}"}, {})
	return _organisation()


@frappe.whitelist(methods=["POST"])
def create_fiscal_year(year, year_start_date, year_end_date, companies=None):
	"""A new fiscal year, for every company or the ones named. Frappe refuses overlaps."""
	_only()
	companies = _dedupe(_json(companies, []) or [])
	unknown = [c for c in companies if not frappe.db.exists("Company", c)]
	if unknown:
		return _refuse("not_found", _("No company {0}.").format(", ".join(unknown)))
	if not cstr(year).strip():
		return _refuse("invalid_setting", _("A fiscal year needs a name, e.g. 2027."))
	if frappe.db.exists("Fiscal Year", cstr(year).strip()):
		return _refuse("duplicate", _("There is already a fiscal year {0}.").format(year))
	doc = frappe.get_doc(
		{
			"doctype": "Fiscal Year",
			"year": cstr(year).strip(),
			"year_start_date": year_start_date,
			"year_end_date": year_end_date,
			"companies": [{"company": c} for c in companies],
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert()
	_record("fiscal year created", "Fiscal Year", doc.name, {}, {"from": year_start_date, "to": year_end_date, "companies": companies})
	return _organisation()


# --- my preferences (every signed-in staff user, about themselves only) ----------------------------

MY_USER_FIELDS = ("language", "time_zone", "desk_theme")
MY_NOTIFICATION_FIELDS = (
	"enabled",
	"enable_email_notifications",
	"enable_email_mention",
	"enable_email_assignment",
	"enable_email_share",
	"enable_email_event_reminders",
	"enable_email_threads_on_assigned_document",
)


def _my_preferences():
	from frappe.core.doctype.user.user import get_timezones
	from frappe.desk.doctype.notification_settings.notification_settings import create_notification_settings

	user = frappe.session.user
	if not frappe.db.exists("Notification Settings", user):
		create_notification_settings(user)
	u = frappe.db.get_value("User", user, list(MY_USER_FIELDS), as_dict=True)
	ns = frappe.db.get_value("Notification Settings", user, list(MY_NOTIFICATION_FIELDS), as_dict=True)
	return {
		"user": user,
		"language": u.language or None,
		"time_zone": u.time_zone or None,
		"desk_theme": u.desk_theme or "Light",
		"notifications": {f: bool(ns.get(f)) for f in MY_NOTIFICATION_FIELDS},
		"languages": frappe.get_all("Language", filters={"enabled": 1}, fields=["name as value", "language_name as label"], order_by="language_name asc"),
		"time_zones": get_timezones()["timezones"],
		"themes": ["Light", "Dark", "Automatic"],
	}


def _staff():
	if frappe.session.user == "Guest" or frappe.get_cached_value("User", frappe.session.user, "user_type") != "System User":
		frappe.throw(_("Sign in as a staff user."), frappe.PermissionError)


@frappe.whitelist(methods=["GET"])
def my_preferences():
	_staff()
	return _my_preferences()


@frappe.whitelist(methods=["POST"])
def save_my_preferences(values):
	"""Your own language, time zone, Desk theme and which e-mails you get. Never anybody else's."""
	_staff()
	from frappe.core.doctype.user.user import get_timezones

	values = _json(values, {}) or {}
	notifications = values.pop("notifications", None) or {}
	extra = sorted(set(values) - set(MY_USER_FIELDS)) + sorted(set(notifications) - set(MY_NOTIFICATION_FIELDS))
	if extra:
		return _refuse("field_not_allowed", _("These cannot be changed here: {0}.").format(", ".join(extra)))
	if values.get("language") and not frappe.db.exists("Language", {"name": values["language"], "enabled": 1}):
		return _refuse("invalid_setting", _("Not a language: {0}.").format(values["language"]))
	if values.get("time_zone") and values["time_zone"] not in get_timezones()["timezones"]:
		return _refuse("invalid_setting", _("Not a time zone: {0}.").format(values["time_zone"]))
	if values.get("desk_theme") and values["desk_theme"] not in ("Light", "Dark", "Automatic"):
		return _refuse("invalid_setting", _("Theme is Light, Dark or Automatic."))
	user = frappe.session.user
	if values:
		doc = frappe.get_doc("User", user)
		doc.update({f: (v or None) for f, v in values.items()})
		doc.flags.ignore_permissions = True
		doc.save()
	if notifications:
		ns = frappe.get_doc("Notification Settings", user)
		ns.update({f: 1 if v else 0 for f, v in notifications.items()})
		ns.flags.ignore_permissions = True
		ns.save()
	return _my_preferences()
