import frappe
from frappe.permissions import add_permission, update_permission_property

# Role permissions on STANDARD (ERPNext / HRMS) doctypes that FoLT's custom roles have to
# touch. Without these, an approver's Desk sidebar simply doesn't show the document they are
# supposed to act on -- sidebar and workspace links are filtered per user by read permission
# (frappe/boot.py:get_sidebar_items -> DeskViews.is_item_allowed), so "no permission" reads as
# "no link". The workflow transitions in fixtures/workflow.json are the source of truth for
# who needs what:
#
#   FoLT Purchase Order Approval      Finance Manager approves Pending Approval -> Approved (submit)
#   Employee Advance Float Approval   Finance Officer checks (draft), Executive Director approves (submit)
#   FoLT Payroll Approval             Finance Assistant drafts, Finance Officer rejects, ED approves (submit)
#
# Permissions for FoLT's OWN doctypes are NOT here -- they live in each doctype's .json, which
# `bench migrate` re-imports, so they stay standard DocPerms.
#
# Trade-off to be aware of: Frappe resolves a doctype's permissions from Custom DocPerm as soon
# as ONE such row exists for it (frappe/model/meta.py:set_custom_permissions), and
# `add_permission` therefore copies the whole standard permission set into Custom DocPerm first.
# From then on this site pins the permissions of the doctypes listed below, i.e. upstream
# changes to their default roles no longer apply. That is the same thing the Role Permissions
# Manager does when you tick a box in the UI; doing it here keeps it version-controlled and
# reproducible on a fresh site instead of being a one-off Desk edit.
WORKFLOW_PERMISSIONS = {
	"Purchase Order": {
		"Finance Manager": ("read", "write", "submit"),
	},
	"Employee Advance": {
		"Finance Officer": ("read", "write"),
		"Executive Director": ("read", "write", "submit"),
	},
	"Salary Slip": {
		"Finance Assistant": ("read", "write", "create"),
		"Finance Officer": ("read", "write"),
		"Executive Director": ("read", "write", "submit"),
	},
}

# `select` lets a role pick a record in a Link field without granting access to its list view
# (frappe/desk/search.py checks "select" when that is all the role has). These are the masters
# referenced by Link fields on the FoLT forms, so the people who fill those forms can actually
# complete them: Activity Requisition -> Project / Cost Center, Derogation Waiver Request ->
# Supplier / Project / Cost Center, Participant Reimbursement List -> Project.
LINK_FIELD_PERMISSIONS = {
	"Project": {
		role: ("select",)
		for role in (
			"Employee",
			"Head of Programs",
			"Head of Finance",
			"Finance Officer",
			"Finance Assistant",
			"Operations Support Officer",
		)
	},
	"Cost Center": {
		role: ("select",)
		for role in ("Employee", "Head of Programs", "Head of Finance", "Operations Support Officer")
	},
	"Supplier": {
		role: ("select",) for role in ("Operations Support Officer", "Procurement Committee", "Head of Finance")
	},
	# The committee and the Head of Finance award against the quotations, so they need to read
	# them, not just reference them.
	"Supplier Quotation": {
		role: ("read",) for role in ("Procurement Committee", "Head of Finance")
	},
	"Request for Quotation": {
		role: ("read",) for role in ("Procurement Committee", "Head of Finance")
	},
}


# Check fields a fresh Custom DocPerm comes with already ticked.
DEFAULT_ON_PTYPES = ("read", "export")


def apply_role_permissions():
	"""Grant FoLT's custom roles the permissions their workflow steps need.

	Idempotent and safe to run on every migrate: a role that already has the permission is
	left alone, so this is a no-op once applied.
	"""
	changed = False
	for perm_map in (WORKFLOW_PERMISSIONS, LINK_FIELD_PERMISSIONS):
		for doctype, roles in perm_map.items():
			if not frappe.db.exists("DocType", doctype):
				continue
			for role, ptypes in roles.items():
				if not frappe.db.exists("Role", role):
					continue
				if _grant(doctype, role, ptypes):
					changed = True
	if changed:
		frappe.clear_cache()


def _grant(doctype, role, ptypes):
	"""Ensure `role` has `ptypes` on `doctype`. Return True if anything was written."""
	missing = [ptype for ptype in ptypes if not _has_perm(doctype, role, ptype)]
	if not missing:
		return False

	new_row = not _perm_row(doctype, role)
	if new_row:
		# Creates the Custom DocPerm row (after copying the standard perms across) with the
		# first missing permission already set.
		add_permission(doctype, role, 0, ptype=missing.pop(0))

	for ptype in missing:
		update_permission_property(doctype, role, 0, ptype, 1)

	if new_row:
		# Custom DocPerm ticks `read` and `export` by default, which would quietly turn a
		# select-only grant into list access. Only rows this function just created are
		# trimmed -- a permission that was already there, standard or not, is left alone.
		for ptype in DEFAULT_ON_PTYPES:
			if ptype not in ptypes and _has_perm(doctype, role, ptype):
				update_permission_property(doctype, role, 0, ptype, 0)

	# get_meta caches per doctype; drop it so a later role in the same run sees the new rows.
	frappe.clear_cache(doctype=doctype)
	return True


def _perm_row(doctype, role):
	"""Return the resolved permlevel-0 rule for `role`, or None.

	Reads through Meta so it sees whichever set is actually in force -- Custom DocPerm when the
	doctype has any, the doctype's own DocPerms otherwise.
	"""
	for perm in frappe.get_meta(doctype).permissions:
		if perm.role == role and perm.permlevel == 0 and not perm.if_owner:
			return perm


def _has_perm(doctype, role, ptype):
	perm = _perm_row(doctype, role)
	return bool(perm and perm.get(ptype))
