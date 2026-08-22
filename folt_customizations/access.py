import frappe

# Which module icons and workspaces each role may see.
#
# Two surfaces decide what a user finds in the Desk, and they are enforced independently:
#
#   Desktop Icon   the module icons on the /apps screen and the icon rail. Shown when the
#                  user can read at least one item in the matching sidebar AND -- if the
#                  icon's `roles` table is populated -- when they hold one of those roles
#                  (frappe/desk/doctype/desktop_icon/desktop_icon.py:get_desktop_icons).
#   Workspace      the sidebar page list. Shown when the workspace's own `roles` table is
#                  empty or the user holds one of them (desktop.py:Workspace.is_permitted).
#
# In both cases an EMPTY roles table means "everyone", which is how a fresh site ships: before
# this module every role saw between 10 and 25 module icons, including Accounting, Payroll, HR
# and Users, whatever their actual job. Per-doctype permissions were doing their work -- the
# pages behind those icons were largely empty -- but an icon that opens an empty page still
# tells a Procurement Committee member that a Payroll module exists and invites them to poke at
# it. This closes the gap between "cannot read the documents" and "cannot see the module".
#
# `Desktop Icon.hidden` is deliberately not used: get_desktop_icons does not filter on it (the
# CRM, Home and FoLT ERP icons all ship with hidden=1 and were being served to every user
# anyway), so `roles` is the only lever that holds server-side.
#
# Kept in code rather than as Desk edits or fixtures for the same reason as workspaces.py: a
# fixture would overwrite the whole upstream Workspace, and `bench migrate` re-syncs both
# doctypes from the shipping apps -- so this is re-applied by the after_migrate hook.

# Every mapping below is widened with System Manager. An administrator locked out of the module
# that grants access is a support call, and Administrator holds every role in any case.
SYSTEM = ("System Manager",)

# Everyone who does day-to-day work in the FoLT app. The FoLT sidebar's items are already
# filtered per doctype permission, so this decides only who sees the tile at all -- a Finance
# Assistant opening it finds Salary Slip, a committee member finds the evaluations.
FOLT_STAFF = (
    "Employee",
    "Purchase User",
    "Purchase Manager",
    "Head of Programs",
    "Head of Finance",
    "Finance Manager",
    "Finance Officer",
    "Finance Assistant",
    "Executive Director",
    "Procurement Committee",
    "Operations Support Officer",
)

# Competitive bidding, from the requisition through the RFQ to the award (Implementation Guide
# section 4). Head of Programs is not here: they approve requisitions inside the FoLT module and
# never touch a Purchase Order.
PROCUREMENT = (
    "Purchase User",
    "Purchase Manager",
    "Procurement Committee",
    "Operations Support Officer",
    "Head of Finance",
    "Finance Manager",
    "Executive Director",
)

# The ledger: invoices, payments, budgets, the chart of accounts and the financial reports.
FINANCE = (
    "Head of Finance",
    "Finance Manager",
    "Finance Officer",
    "Finance Assistant",
    "Executive Director",
    "Accounts User",
    "Accounts Manager",
)

# Salary Slips and the statutory components. Drafted by the Finance Assistant, rejected by the
# Finance Officer, approved by the Executive Director (FoLT Payroll Approval).
PAYROLL = (
    "Finance Assistant",
    "Finance Officer",
    "Executive Director",
    "HR User",
    "HR Manager",
)

# Employee lifecycle, recruitment, attendance. No FoLT workflow touches these, so no FoLT role
# is listed -- they are for whoever ends up holding the HR roles.
HR = ("HR User", "HR Manager")

# Expense claims and staff advances are raised by whoever spent the money, so every member of
# staff needs the tile -- unlike the rest of the HR module.
STAFF_CLAIMS = (
    "Employee",
    "Finance Officer",
    "Finance Assistant",
    "Executive Director",
    "HR User",
    "HR Manager",
)

# Modules with no transactions on this site and no FoLT process behind them (measured: zero
# records in Lead/Opportunity/Customer, Sales Order/Invoice, Stock Entry/Delivery Note, Work
# Order/BOM, Quality Inspection, Asset, Subcontracting Order and Issue). Locked to System
# Manager rather than deleted: an NGO that starts selling training or tracking assets re-opens
# one line here, and nothing has been thrown away in the meantime.
UNUSED = SYSTEM

# Icon or workspace label -> the roles allowed to see it. A label absent from this map keeps
# frappe's default of "everyone", so anything added by a future ERPNext version stays visible
# until it is classified here -- deliberately fail-open, because a missing icon is a silent
# fault and a visible one is not.
MODULE_ACCESS = {
    # --- FoLT's own working surface ---
    "FoLT": FOLT_STAFF,
    # A near-empty duplicate of the FoLT workspace (one Activity Requisition shortcut), kept
    # visible to the same people rather than removed -- deleting somebody's workspace is their
    # call, not this file's.
    "Procurement & Finance": FOLT_STAFF,
    # --- app tiles, which gate their children: a child icon is dropped when its parent is ---
    "FoLT ERP": tuple({*PROCUREMENT, *FINANCE}),
    "FoLT HR": tuple({*PAYROLL, *HR, *STAFF_CLAIMS}),
    "System Admin": SYSTEM,
    # --- procurement ---
    "Buying": PROCUREMENT,
    # --- the ledger ---
    "Accounting": FINANCE,
    "Accounts Setup": FINANCE,
    "Banking": FINANCE,
    "Budget": FINANCE,
    "Financial Reports": FINANCE,
    "Invoicing": FINANCE,
    "Payments": FINANCE,
    "Taxes": FINANCE,
    # --- payroll ---
    "Payroll": PAYROLL,
    "Tax & Benefits": PAYROLL,
    # --- HR ---
    "Expenses": STAFF_CLAIMS,
    "Leaves": ("Employee", *HR),
    "HR Setup": HR,
    "Organization": HR,
    "Performance": HR,
    "Recruitment": HR,
    "Shift & Attendance": HR,
    "Tenure": HR,
    # --- modules FoLT does not use ---
    "Assets": UNUSED,
    "CRM": UNUSED,
    "Manufacturing": UNUSED,
    "Projects": UNUSED,
    "Quality": UNUSED,
    "Selling": UNUSED,
    "Share Management": UNUSED,
    "Stock": UNUSED,
    "Subcontracting": UNUSED,
    "Subscription": UNUSED,
    "Support": UNUSED,
    # --- administration: configuring the system is not a FoLT job function ---
    "Automation": SYSTEM,
    "Build": SYSTEM,
    "Data": SYSTEM,
    "Email": SYSTEM,
    "ERPNext Settings": SYSTEM,
    "FoLT Settings": SYSTEM,
    # ERPNext's setup landing page -- Customer, Lead, Item, Stock Reconciliation, Data Import.
    "Home": SYSTEM,
    "Integrations": SYSTEM,
    "Printing": SYSTEM,
    "System": SYSTEM,
    "Users": SYSTEM,
    "Website": SYSTEM,
}

# "My Workspaces" is left alone on purpose: get_sidebar_items short-circuits it for every user,
# so restricting it would only be theatre.
UNRESTRICTED = {"My Workspaces", "Welcome Workspace"}

TARGET_DOCTYPES = ("Workspace", "Desktop Icon")


def apply_module_access():
    """Restrict every module icon and workspace to the roles that have a use for it.

    Idempotent and safe on every migrate: a row already carrying the target role set is left
    alone, so a second run writes nothing.
    """
    changed = False
    for label, roles in MODULE_ACCESS.items():
        if label in UNRESTRICTED:
            continue
        allowed = _existing_roles({*roles, *SYSTEM})
        if not allowed:
            # Every role in the mapping is missing from this site -- writing an empty table
            # would read as "everyone", the opposite of what is meant here.
            continue
        for doctype in TARGET_DOCTYPES:
            for name in _targets(doctype, label):
                changed |= _set_roles(doctype, name, allowed)
    if changed:
        frappe.clear_cache()
    return changed


def _existing_roles(roles):
    """Drop roles this site has never had -- ERPNext modules FoLT does not install, mostly."""
    return {role for role in roles if frappe.db.exists("Role", role)}


def _targets(doctype, label):
    """The rows for one module label. Desktop Icons are matched on `label`, not on `name`.

    A Desktop Icon's name is autogenerated for anything a user created from a workspace, so the
    label is the only stable handle -- and more than one row can carry it (a folder and the link
    inside it), which is why this returns a list.
    """
    if doctype == "Workspace":
        return frappe.get_all("Workspace", filters={"name": label}, pluck="name")
    return frappe.get_all("Desktop Icon", filters={"label": label}, pluck="name")


def _set_roles(doctype, name, allowed):
    """Replace the `roles` child table of one Workspace / Desktop Icon. True if it changed.

    Written straight to the child table rather than through a parent `save()`: saving a
    Workspace rewrites its content blocks and bumps its timestamp, which is a lot of blast
    radius for one permission row.

    `db_insert()` rather than `insert()`, and that is not an optimisation. HasRole.before_insert
    rejects a duplicate on `{parent, role}` alone, with no `parenttype` in the filter -- a check
    written for User.roles, where the parent is an email address. Workspaces and Desktop Icons
    share their names by design ("FoLT", "Buying", "Payroll" are all both), so the second of the
    pair is refused as "User 'FoLT' already has the role 'Employee'". db_insert is the same call
    frappe makes for child rows during a parent save, and skips the controller hook with it.
    """
    current = set(
        frappe.get_all(
            "Has Role", filters={"parenttype": doctype, "parent": name}, pluck="role"
        )
    )
    if current == allowed:
        return False

    frappe.db.delete("Has Role", {"parenttype": doctype, "parent": name})
    for idx, role in enumerate(sorted(allowed), start=1):
        row = frappe.new_doc("Has Role")
        row.update(
            {
                "parent": name,
                "parenttype": doctype,
                "parentfield": "roles",
                "role": role,
                "idx": idx,
            }
        )
        row.set_new_name()
        row.db_insert()
    return True


def visible_modules(user):
    """The module icons and workspaces `user` can actually see, read out of the boot payload.

    The boot payload rather than a re-implementation of the rules: this is the same data the
    Desk renders its icon rail from, so it cannot drift from what the user sees. Used by
    access_e2e to check the mapping above against every role.
    """
    import frappe.boot

    original = frappe.session.user
    try:
        frappe.set_user(user)
        # get_desktop_icons and the bootinfo are both cached per user for six hours.
        frappe.cache.hdel("desktop_icons", user)
        frappe.cache.hdel("bootinfo", user)
        boot = frappe.boot.get_bootinfo()
        sidebars = boot.get("workspace_sidebar_item") or {}
        return {
            "icons": sorted({icon["label"] for icon in (boot.get("desktop_icons") or [])}),
            "workspaces": sorted(
                {page["title"] for page in (boot.get("workspaces") or {}).get("pages", [])}
            ),
            # Section Breaks are headings, not destinations, and get_sidebar_items lets them
            # through unfiltered -- counting them would read as access to something.
            "sidebars": {
                name: [item["label"] for item in sidebar["items"] if item["type"] != "Section Break"]
                for name, sidebar in sidebars.items()
            },
        }
    finally:
        frappe.set_user(original)
