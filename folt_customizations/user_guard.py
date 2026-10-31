"""No save may leave the site without an enabled System Manager.

The app's Settings screens refuse it before they write (admin_portal), but a User is also saved
from the Desk, over REST, and by other apps' hooks (erpnext's Employee.update_user, hrms's
update_approver_user_roles). This validate hook is the backstop for all of them: it counts the
enabled System Managers there would be after this save and refuses one that leaves none.

Administrator and Guest are never counted. User's permission_query_conditions hides them from
everyone else, and Administrator is not a person who can be reached; the BFF counts the same way.

Skipped during install, migrate and patches (fixtures and setup wizards save Users before any
System Manager exists), and when the site has none to begin with -- the guard keeps one, it does
not demand one.
"""

import frappe
from frappe import _

SYSTEM_MANAGER = "System Manager"
STANDARD_USERS = ("Administrator", "Guest")


def holds_system_manager(doc):
	return bool(doc and doc.enabled and any(r.role == SYSTEM_MANAGER for r in doc.get("roles") or []))


def other_system_managers(user):
	"""Enabled System Managers other than `user` (Administrator and Guest excluded)."""
	return frappe.db.sql(
		"""select count(distinct u.name) from `tabUser` u
		join `tabHas Role` r on r.parent = u.name and r.parenttype = 'User'
		where r.role = %s and u.enabled = 1 and u.name not in %s and u.name != %s""",
		(SYSTEM_MANAGER, STANDARD_USERS, user),
	)[0][0]


def keep_a_system_manager(doc, method=None):
	if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch:
		return
	if doc.name in STANDARD_USERS or doc.is_new():
		return
	before = doc.get_doc_before_save()
	if not holds_system_manager(before) or holds_system_manager(doc):
		return
	if not other_system_managers(doc.name):
		frappe.throw(
			_("{0} is the last enabled System Manager. Give another user the role first.").format(doc.name),
			frappe.PermissionError,
			title=_("Last System Manager"),
		)
