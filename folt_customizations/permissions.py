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
#                                     ...and needs read on Supplier to do it: ERPNext re-reads
#                                     party details on every save, so submit alone is unusable.
#   Employee Advance Float Approval   Finance Officer checks (draft), Executive Director approves (submit)
#   FoLT Payroll Approval             Finance Assistant drafts, Finance Officer rejects, ED approves (submit)
#   FoLT Float Retirement Approval    Finance Officer reviews (draft), ED approves (submit),
#                                     Finance Assistant settles (after submit) -- plus permlevel 1
#                                     on approval_status, see PERMLEVEL_1_PERMISSIONS below.
#   Employee Advance Float Approval   ...and after approval: Finance Assistant records the
#                                     disbursement, Head of Finance flags overdue and closes
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
	# Not decoration on the grant above -- its prerequisites. ERPNext re-derives party, item and
	# account details on every Purchase Order save, and several of those reads are
	# permission-checked and THROW rather than degrade. Miss any one and the Finance Manager's
	# Approve step dies with a bare PermissionError partway through validation, so the `submit`
	# granted above can never be exercised:
	#
	#   Supplier   party.py:142            has_permission(party_type, "read", party, throw=True)
	#   Address    party.py:225/245        render_address() -> Address.check_permission()
	#   Item       get_item_details.py:87  get_cached_doc("Item", ...).check_permission()
	#   Account    party.py:432            account_perm_check() -- accepts "select" OR "read"
	#
	# Account gets `select` rather than `read`, which account_perm_check() treats as equivalent:
	# the lookup is internal to the save, so an approver has no reason to browse the chart of
	# accounts. Everything else here is read-only and only on masters the order being approved
	# already displays.
	#
	# This list was arrived at by running an approval as a Finance Manager and following the
	# tracebacks, one grant at a time -- the checks live in four different modules and no single
	# grep finds them all. If ERPNext adds one, the symptom is the same bare PermissionError, so
	# reproduce it the same way rather than guessing.
	"Supplier": {
		"Finance Manager": ("read",),
	},
	"Address": {
		"Finance Manager": ("read",),
	},
	"Item": {
		"Finance Manager": ("read",),
	},
	"Account": {
		"Finance Manager": ("select",),
	},
	# The float's life after approval is now tracked on the advance itself -- Disbursed,
	# Overdue, Accounted, Closed -- and every one of those is a transition on an already
	# submitted document. Frappe has no permission of its own for that: `check_if_latest` maps
	# an update-after-submit to the `submit` ptype (document.py:check_docstatus_transition), so
	# a role that only has `write` gets a bare PermissionError the moment it tries to move the
	# state. Hence `submit` on roles that never approve a float: the doctype permission decides
	# whether they may write to a submitted document at all, and the workflow decides which
	# transition each of them may actually make. Frappe also hides the standard Submit button
	# outright while a workflow is active, so this does not hand anybody an approval route
	# around the chain.
	"Employee Advance": {
		"Finance Officer": ("read", "write", "submit"),
		"Executive Director": ("read", "write", "submit"),
		"Finance Assistant": ("read", "write", "submit"),
		"Head of Finance": ("read", "write", "submit"),
	},
	# Step 4 of the finance workflow -- float accountability -- runs on the Expense Claim.
	# hrms ships it with permissions for HR roles and Expense Approver, none of which FoLT
	# uses; the retirement chain is Finance Officer -> Executive Director, then the Finance
	# Assistant settles the balance after submission (hence `submit` there too, per above).
	"Expense Claim": {
		"Finance Officer": ("read", "write"),
		"Executive Director": ("read", "write", "submit"),
		"Finance Assistant": ("read", "write", "submit"),
	},
	"Salary Slip": {
		"Finance Assistant": ("read", "write", "create"),
		"Finance Officer": ("read", "write"),
		"Executive Director": ("read", "write", "submit"),
	},
}

# Expense Claim.approval_status sits at permlevel 1, and the retirement workflow writes it
# (the Approved and Rejected states carry update_field). Frappe silently reverts a permlevel
# field the acting user cannot write -- `Document.validate_higher_perm_levels` resets it to the
# stored value -- so without these grants the Executive Director's Approve would save, the
# field would snap back to Draft, and the submit would then fail with hrms's own
# "Approval Status must be 'Approved' or 'Rejected'". Read comes with it so the field is
# visible to the person being asked to act on it.
PERMLEVEL_1_PERMISSIONS = {
	"Expense Claim": {
		"Finance Officer": ("read", "write"),
		"Executive Director": ("read", "write"),
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
	for perm_map, permlevel in (
		(WORKFLOW_PERMISSIONS, 0),
		(LINK_FIELD_PERMISSIONS, 0),
		(PERMLEVEL_1_PERMISSIONS, 1),
	):
		for doctype, roles in perm_map.items():
			if not frappe.db.exists("DocType", doctype):
				continue
			for role, ptypes in roles.items():
				if not frappe.db.exists("Role", role):
					continue
				if _grant(doctype, role, ptypes, permlevel):
					changed = True
	if changed:
		frappe.clear_cache()


def _grant(doctype, role, ptypes, permlevel=0):
	"""Ensure `role` has `ptypes` on `doctype`. Return True if anything was written."""
	missing = [ptype for ptype in ptypes if not _has_perm(doctype, role, ptype, permlevel)]
	if not missing:
		return False

	new_row = not _perm_row(doctype, role, permlevel)
	if new_row:
		# Creates the Custom DocPerm row (after copying the standard perms across) with the
		# first missing permission already set.
		add_permission(doctype, role, permlevel, ptype=missing.pop(0))

	for ptype in missing:
		update_permission_property(doctype, role, permlevel, ptype, 1)

	if new_row:
		# Custom DocPerm ticks `read` and `export` by default, which would quietly turn a
		# select-only grant into list access. Only rows this function just created are
		# trimmed -- a permission that was already there, standard or not, is left alone.
		for ptype in DEFAULT_ON_PTYPES:
			if ptype not in ptypes and _has_perm(doctype, role, ptype, permlevel):
				update_permission_property(doctype, role, permlevel, ptype, 0)

	# get_meta caches per doctype; drop it so a later role in the same run sees the new rows.
	frappe.clear_cache(doctype=doctype)
	return True


def _perm_row(doctype, role, permlevel=0):
	"""Return the resolved rule for `role` at `permlevel`, or None.

	Reads through Meta so it sees whichever set is actually in force -- Custom DocPerm when the
	doctype has any, the doctype's own DocPerms otherwise.
	"""
	for perm in frappe.get_meta(doctype).permissions:
		if perm.role == role and perm.permlevel == permlevel and not perm.if_owner:
			return perm


def _has_perm(doctype, role, ptype, permlevel=0):
	perm = _perm_row(doctype, role, permlevel)
	return bool(perm and perm.get(ptype))
