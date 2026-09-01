"""Check that every role sees the module icons it needs, and nothing else.

Two properties, and they pull in opposite directions:

  containment   no role is offered a module icon or workspace that access.MODULE_ACCESS does
                not grant it. This is the one that was failing -- before the mapping, a single
                FoLT role saw between 10 and 25 module icons.
  sufficiency   each role still reaches the modules its workflow steps live in. A mapping that
                hides everything passes containment and is useless.

Read out of the boot payload (access.visible_modules), so it measures what the Desk actually
renders rather than re-deriving the permission rules and agreeing with itself.

    bench --site <site> execute folt_customizations.access_e2e.run
    bench --site <site> execute folt_customizations.access_e2e.report
"""

import frappe

from folt_customizations.access import (
    MODULE_ACCESS,
    SYSTEM,
    UNRESTRICTED,
    retired_modules,
    visible_modules,
)

# The one-role-per-user test logins (see seed_test_users.py), and the modules each of them has
# to be able to reach. Deliberately spelled out rather than derived from MODULE_ACCESS: this is
# the requirement, and the mapping is the implementation of it.
ROLE_EXPECTATIONS = {
    # The one-role-per-user test logins (see seed_test_users.py). `must_see` is the module icons
    # the role needs; `must_reach` and `must_not_reach` are items in the FoLT sidebar, which is
    # where FoLT's own work lives. Both are read off the workflow transitions in
    # fixtures/workflow.json -- that file says who acts on what, and this says they can find it.
    "requester.test@folt.test": {
        "role": "Employee",
        "must_see": ["FoLT", "Expenses"],
        # Drafts requisitions, attendance registers and reimbursement lists; requests advances.
        "must_reach": [
            "Activity Requisition",
            "Employee Advance",
            "Attendance Register",
            "Participant Reimbursement",
        ],
        "must_not_reach": ["Purchase Order", "Committee Evaluation", "Derogation Waiver Request"],
    },
    "hop.test@folt.test": {
        "role": "Head of Programs",
        "must_see": ["FoLT"],
        # Approves requisitions and verifies the attendance register.
        "must_reach": ["Activity Requisition", "Attendance Register"],
        "must_not_reach": ["Purchase Order", "Salary Slip", "Committee Evaluation"],
    },
    "hof.test@folt.test": {
        "role": "Head of Finance",
        "must_see": ["FoLT", "Buying"],
        # Approves requisitions, and awards on the committee's recommendation.
        "must_reach": ["Activity Requisition", "Committee Evaluation"],
        "must_not_reach": ["Salary Slip"],
    },
    "purchaser.test@folt.test": {
        "role": "Purchase User",
        "must_see": ["FoLT", "Buying"],
        # Raises the order and opens the evaluation.
        "must_reach": ["Purchase Order", "Committee Evaluation"],
        "must_not_reach": ["Salary Slip", "Employee Advance"],
    },
    "committee.test@folt.test": {
        "role": "Procurement Committee",
        "must_see": ["FoLT", "Buying"],
        # Scores the bids and endorses waivers.
        "must_reach": ["Committee Evaluation", "Derogation Waiver Request"],
        "must_not_reach": ["Salary Slip", "Purchase Order", "Employee Advance"],
    },
    "finmanager.test@folt.test": {
        "role": "Finance Manager",
        # Approves Purchase Orders and nothing else -- no FoLT doctype carries a permission for
        # this role, which is why the FoLT workspace page itself stays out of their sidebar list
        # (desktop.py raises PermissionError for a workspace whose module the user cannot touch).
        "must_see": ["Buying"],
        "must_reach": ["Purchase Order"],
        "must_not_reach": ["Salary Slip", "Committee Evaluation", "Employee Advance"],
    },
    "oso.test@folt.test": {
        "role": "Operations Support Officer",
        "must_see": ["FoLT"],
        # Raises single-source waiver requests.
        "must_reach": ["Derogation Waiver Request"],
        "must_not_reach": ["Salary Slip", "Purchase Order", "Committee Evaluation"],
    },
    "finofficer.test@folt.test": {
        "role": "Finance Officer",
        "must_see": ["FoLT"],
        # Checks advances, reviews waivers, verifies reimbursements, rejects payroll.
        "must_reach": [
            "Employee Advance",
            "Derogation Waiver Request",
            "Participant Reimbursement",
            "Salary Slip",
        ],
        "must_not_reach": ["Committee Evaluation"],
    },
    "ed.test@folt.test": {
        "role": "Executive Director",
        "must_see": ["FoLT"],
        # Approves advances and payroll, authorises waivers.
        "must_reach": ["Employee Advance", "Salary Slip", "Derogation Waiver Request"],
        "must_not_reach": ["Committee Evaluation"],
    },
    "finassistant.test@folt.test": {
        "role": "Finance Assistant",
        "must_see": ["FoLT"],
        # Drafts payroll and records reimbursement payouts.
        "must_reach": ["Salary Slip", "Participant Reimbursement"],
        "must_not_reach": ["Purchase Order", "Committee Evaluation", "Derogation Waiver Request"],
    },
}

# The FoLT sidebar is the one every FoLT role works out of, so the item-level checks above are
# read from it. Its key in the boot payload is the sidebar name, lowercased.
FOLT_SIDEBAR = "folt"

# Sidebar items every FoLT role must reach, whatever its steps are. Stated here rather than
# repeated in all ten must_reach lists, so it cannot end up true of nine roles and quietly not the
# tenth. My Tasks is the post-login landing page (hooks.add_to_apps_screen) and the Page carries
# no `roles`, so a role that cannot reach it lands somewhere it was not meant to.
ALWAYS_REACHABLE = ["My Tasks"]

# Modules nobody outside System Manager has any business being offered. Checked by name as well
# as through the mapping, because these are the ones that matter: user administration, the
# doctype builder, and the modules FoLT does not run at all.
NEVER_FOR_STAFF = [
    "Users", "Build", "System", "Website", "Integrations", "Automation", "Email", "Printing",
    "Data", "Home", "FoLT Settings", "ERPNext Settings", "System Admin",
    "CRM", "Selling", "Stock", "Manufacturing", "Quality", "Assets", "Subcontracting",
    "Support", "Share Management", "Subscription", "Projects",
]

PASS, FAIL = [], []


def check(label, condition, detail=""):
    (PASS if condition else FAIL).append(label)
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")


def allowed_for(user):
    """The labels MODULE_ACCESS grants `user`, plus the ones it deliberately does not manage."""
    roles = set(frappe.get_roles(user))
    granted = {
        label
        for label, label_roles in MODULE_ACCESS.items()
        if roles & {*label_roles, *SYSTEM}
    }
    return granted | UNRESTRICTED


def report():
    """Print what every test user can see. No assertions -- this is the audit."""
    for user in ROLE_EXPECTATIONS:
        if not frappe.db.exists("User", user):
            print(f"\n{user}: not seeded")
            continue
        seen = visible_modules(user)
        allowed = allowed_for(user)
        unmapped = [i for i in seen["icons"] if i not in MODULE_ACCESS and i not in UNRESTRICTED]
        print(f"\n{user}  ({', '.join(r for r in frappe.get_roles(user) if r not in ('All', 'Guest'))})")
        print(f"  icons      ({len(seen['icons'])}): {seen['icons']}")
        print(f"  workspaces ({len(seen['workspaces'])}): {seen['workspaces']}")
        print(f"  FoLT items : {seen['sidebars'].get(FOLT_SIDEBAR, [])}")
        over = sorted(set(seen["icons"]) - allowed)
        if over:
            print(f"  NOT GRANTED BY THE MAPPING: {over}")
        if unmapped:
            print(f"  unmapped (visible by default): {unmapped}")


def run():
    print("\nModule access — one role per user, boot payload as rendered\n")
    seeded = [user for user in ROLE_EXPECTATIONS if frappe.db.exists("User", user)]
    check("test users are seeded", len(seeded) == len(ROLE_EXPECTATIONS), f"{len(seeded)}/{len(ROLE_EXPECTATIONS)}")

    unmapped_seen = {}
    for user in seeded:
        expectation = ROLE_EXPECTATIONS[user]
        role = expectation["role"]
        seen = visible_modules(user)
        icons, workspaces = set(seen["icons"]), set(seen["workspaces"])
        allowed = allowed_for(user)

        # containment, on both surfaces
        over_icons = sorted(icons - allowed - set(_unmapped(icons)))
        check(f"{role}: no module icon beyond its mapping", not over_icons, f"extra: {over_icons}" if over_icons else f"{len(icons)} icons")
        over_pages = sorted(workspaces - allowed - set(_unmapped(workspaces)))
        check(f"{role}: no workspace beyond its mapping", not over_pages, f"extra: {over_pages}" if over_pages else f"{len(workspaces)} workspaces")

        # the named offenders
        forbidden = sorted((icons | workspaces) & set(NEVER_FOR_STAFF) - allowed)
        check(f"{role}: no administration or unused-module icon", not forbidden, f"{forbidden}" if forbidden else "")

        # sufficiency, at module level
        missing = [module for module in expectation["must_see"] if module not in icons]
        check(f"{role}: still sees its own module icons", not missing, f"missing: {missing}" if missing else str(expectation["must_see"]))

        # sufficiency and containment at item level, inside the sidebar FoLT works out of
        items = set(seen["sidebars"].get(FOLT_SIDEBAR, []))
        unreachable = [
            item
            for item in ALWAYS_REACHABLE + expectation["must_reach"]
            if item not in items
        ]
        check(
            f"{role}: reaches every document its workflow steps are on",
            not unreachable,
            f"missing: {unreachable}" if unreachable else f"{len(items)} items",
        )
        leaked = [item for item in expectation["must_not_reach"] if item in items]
        check(
            f"{role}: is not offered documents that are not its business",
            not leaked,
            f"offered: {leaked}" if leaked else "",
        )

        unmapped_seen[role] = _unmapped(icons)

    # Retirement, checked on a System Manager rather than on a staff role. UNUSED already kept
    # these icons from staff, so the staff checks above passed throughout the period when every
    # administrator could still see all five -- this is the check that would have caught it.
    # A typo in the seed is silent: hide_workspaces() skips a name with no Workspace, so the
    # module simply stays visible on every new site and nothing says why. Read-only, so it is safe
    # to run anywhere -- unlike the seed path itself, which only fires on a site with no store yet
    # and is therefore exercised by a fresh install rather than from here.
    from folt_customizations.workspaces import SEED_HIDDEN_WORKSPACES

    missing_seed = [name for name in SEED_HIDDEN_WORKSPACES if not frappe.db.exists("Workspace", name)]
    check(
        "every name in the hidden-workspace seed is a real Workspace",
        not missing_seed,
        f"no such workspace: {missing_seed}" if missing_seed else f"{len(SEED_HIDDEN_WORKSPACES)} seeded",
    )

    manager = _a_system_manager()
    retired = retired_modules()
    if manager:
        seen_by_manager = set(visible_modules(manager)["icons"])
        still_offered = sorted(retired & seen_by_manager)
        check(
            "System Manager: no retired module icon",
            not still_offered,
            f"offered: {still_offered}" if still_offered else f"{len(retired)} retired",
        )
    else:
        print("  note: no System Manager besides Administrator -- retirement check skipped")

    # The mapping must never lock an administrator out of the modules that grant access.
    admin = visible_modules("Administrator")
    admin_missing = [
        label for label in ("Users", "Build", "System", "FoLT", "Buying", "Payroll")
        if label not in set(admin["icons"]) | set(admin["workspaces"])
    ]
    check("Administrator still sees the administration modules", not admin_missing, f"missing: {admin_missing}" if admin_missing else "")

    # Not a failure -- the mapping is deliberately fail-open so a module added by a future
    # ERPNext version stays visible rather than vanishing silently. But it should be seen.
    stragglers = {role: labels for role, labels in unmapped_seen.items() if labels}
    if stragglers:
        print("\n  note: icons visible because MODULE_ACCESS does not classify them yet:")
        for role, labels in stragglers.items():
            print(f"    {role}: {labels}")

    print(f"\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED: " + "; ".join(FAIL))
    return {"passed": len(PASS), "failed": len(FAIL), "failures": FAIL}


def run_visibility_roundtrip(workspace="Quality"):
    """Take a module off the Desk from the Desk, and put it back. The regression this closes.

    Un-ticking Public used to move one flag on one row: the workspace left the sidebar page list
    and the module's icon, which does not read that flag, stayed exactly where it was. Ticking it
    back on was worse -- the flag went to 1 and the next migrate put it back to 0, because the
    hidden set was a list in the repo that no Desk edit could reach.

    Driven through a real `doc.save()` rather than through record_visibility_intent directly,
    because the hook wiring is half of what is being tested: an inline edit in the Workspace list
    view is a save, and if doc_events is not carrying it then nothing else here matters.

    Starts and ends on the workspace's original state, and asserts the *intermediate* state in
    both directions, so a failure cannot leave the site half-toggled.

        bench --site <site> execute folt_customizations.access_e2e.run_visibility_roundtrip
    """
    from folt_customizations.workspaces import hidden_workspaces

    print(f"\nDesk visibility round trip on {workspace}\n")
    icon = frappe.db.exists("Desktop Icon", {"label": workspace})
    check(f"{workspace} has a Workspace and a Desktop Icon", bool(frappe.db.exists("Workspace", workspace) and icon))
    if not icon:
        return {"passed": len(PASS), "failed": len(FAIL), "failures": FAIL}

    before = frappe.db.get_value("Workspace", workspace, ["public", "is_hidden"], as_dict=True)
    manager = _a_system_manager()

    try:
        _set_public(workspace, 1)
        check(f"{workspace}: publishing clears both flags", _flags(workspace) == (1, 0), str(_flags(workspace)))
        check(f"{workspace}: publishing drops it from the hidden set", workspace not in hidden_workspaces())
        check(f"{workspace}: its icon is un-hidden", frappe.db.get_value("Desktop Icon", icon, "hidden") == 0)
        check(
            f"{workspace}: its icon is back on the mapping's roles",
            _icon_roles(icon) != {"Administrator"},
            f"roles: {sorted(_icon_roles(icon))}",
        )
        if manager:
            check(
                f"{workspace}: a System Manager is offered the icon again",
                workspace in set(visible_modules(manager)["icons"]),
            )

        _set_public(workspace, 0)
        check(f"{workspace}: hiding sets both flags", _flags(workspace) == (0, 1), str(_flags(workspace)))
        check(f"{workspace}: hiding adds it to the hidden set", workspace in hidden_workspaces())
        check(f"{workspace}: its icon is hidden", frappe.db.get_value("Desktop Icon", icon, "hidden") == 1)
        check(f"{workspace}: its icon is gated to Administrator", _icon_roles(icon) == {"Administrator"})
        if manager:
            check(
                f"{workspace}: a System Manager is no longer offered the icon",
                workspace not in set(visible_modules(manager)["icons"]),
            )
        else:
            print("  note: no System Manager besides Administrator -- boot-payload halves skipped")
    finally:
        _set_public(workspace, before.public)
        frappe.db.set_value("Workspace", workspace, dict(before), update_modified=False)

    check(f"{workspace}: restored to its original flags", _flags(workspace) == (before.public, before.is_hidden))
    print(f"\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED: " + "; ".join(FAIL))
    return {"passed": len(PASS), "failed": len(FAIL), "failures": FAIL}


def run_migrate_durability():
    """A Desk decision has to survive `bench migrate`. This is the half that used to be a list.

    `bench migrate` re-imports every standard workspace from its shipping app, which resets
    public=1 -- that is why workspaces.hide_workspaces() exists and why the hidden set could not
    simply live in tabWorkspace. Before the store, an administrator's tick was undone by the next
    deploy in one direction (they hid something, migrate published it) and by the after_migrate
    hook in the other (they published something, the hook re-hid it from a list in the repo).

    Simulates the re-import rather than running a migrate: the re-import's effect on this is
    exactly `public=1, is_hidden=0` written straight to the row, and then the after_migrate hooks
    run in their hooks.py order. Cheap enough to run on every change, which a real migrate is not.

        bench --site <site> execute folt_customizations.access_e2e.run_migrate_durability
    """
    from folt_customizations.access import apply_module_access
    from folt_customizations.workspaces import hidden_workspaces, hide_workspaces

    print("\nMigrate durability -- upstream re-import, then the after_migrate hooks\n")
    hidden = hidden_workspaces()
    published = [
        name
        for name in frappe.get_all("Workspace", filters={"public": 1}, pluck="name")
        if name not in hidden and frappe.db.exists("Desktop Icon", {"label": name})
    ]
    check("there is something hidden and something published to test with", bool(hidden and published))
    if not (hidden and published):
        return {"passed": len(PASS), "failed": len(FAIL), "failures": FAIL}

    stays_hidden = sorted(hidden)[0]
    stays_visible = sorted(published)[0]
    before = {
        name: frappe.db.get_value("Workspace", name, ["public", "is_hidden"], as_dict=True)
        for name in (stays_hidden, stays_visible)
    }

    try:
        # What the re-import does to both of them, indiscriminately.
        for name in (stays_hidden, stays_visible):
            frappe.db.set_value(
                "Workspace", name, {"public": 1, "is_hidden": 0}, update_modified=False
            )
        frappe.db.commit()

        hide_workspaces()
        apply_module_access()
        frappe.db.commit()

        check(
            f"{stays_hidden}: re-hidden after the re-import published it",
            _flags(stays_hidden) == (0, 1),
            str(_flags(stays_hidden)),
        )
        icon = frappe.db.exists("Desktop Icon", {"label": stays_hidden})
        if icon:
            check(f"{stays_hidden}: its icon is retired again", _icon_roles(icon) == {"Administrator"})
        check(
            f"{stays_visible}: left published, not dragged back into the hidden set",
            _flags(stays_visible) == (1, 0) and stays_visible not in hidden_workspaces(),
            str(_flags(stays_visible)),
        )
    finally:
        for name, flags in before.items():
            frappe.db.set_value("Workspace", name, dict(flags), update_modified=False)
        apply_module_access()
        frappe.db.commit()

    check(
        "both workspaces restored to their original flags",
        all(_flags(name) == (f.public, f.is_hidden) for name, f in before.items()),
    )
    print(f"\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED: " + "; ".join(FAIL))
    return {"passed": len(PASS), "failed": len(FAIL), "failures": FAIL}


def _set_public(workspace, public):
    """Tick or un-tick Public the way the Desk does it -- a save, so doc_events runs."""
    doc = frappe.get_doc("Workspace", workspace)
    doc.public = public
    doc.save()
    frappe.db.commit()


def _flags(workspace):
    return tuple(frappe.db.get_value("Workspace", workspace, ["public", "is_hidden"]))


def _icon_roles(icon):
    return set(
        frappe.get_all("Has Role", filters={"parenttype": "Desktop Icon", "parent": icon}, pluck="role")
    )


def _a_system_manager():
    """An enabled System Manager who is not the Administrator, or None.

    The Administrator is no use for this check: get_roles() hands that login every role on the
    site, including the `Administrator` role the retired icons are gated to, so it sees them by
    design. Not named in the output -- these are real staff accounts.
    """
    for user in frappe.get_all(
        "Has Role",
        filters={"parenttype": "User", "role": "System Manager"},
        pluck="parent",
    ):
        if user != "Administrator" and frappe.db.get_value("User", user, "enabled"):
            return user
    return None


def _unmapped(labels):
    return sorted(label for label in labels if label not in MODULE_ACCESS and label not in UNRESTRICTED)
