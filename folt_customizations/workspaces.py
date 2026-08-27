import frappe

# Standard ERPNext workspaces FoLT does not use, removed from the Desk sidebar.
# Kept in code (not a one-off Desk edit, and not a Workspace JSON fixture) so we touch
# only two flags and leave the rest of each upstream workspace definition untouched --
# a Workspace fixture would overwrite the whole doc and mask ERPNext's own updates to
# these workspaces on version upgrades.
#
# Why both `public=0` AND `is_hidden=1`:
#   frappe/desk/desktop.py:get_workspaces() shows a public page when
#       page.public and (has_access or not page.is_hidden)
#   where has_access = "Workspace Manager" in the user's roles. So is_hidden alone hides
#   a workspace only from NON-managers -- an Administrator / Workspace Manager still sees
#   it. Setting public=0 (with no for_user owner) drops the page from the sidebar for
#   EVERYONE, managers included. is_hidden=1 is kept as belt-and-suspenders for the
#   non-manager path. The workspaces still exist, so their doctypes/reports remain
#   reachable by URL and global search -- only the sidebar icon is gone.
#
# "ERPNext Settings" is in this list for a different reason from the five above: it is not
# unused, it is *superseded*. branding.RELINKED_DESKTOP_ICONS re-points its Desktop Icon at the
# Workspace Sidebar we ship as "FoLT Settings", and the comment there says erpnext's own doc
# "ends up with no Desktop Icon referencing it, which makes it invisible rather than broken".
# That is true of the **Workspace Sidebar** named "ERPNext Settings" -- and only of it. The
# separately named **Workspace** of the same name (module Setup, app erpnext) is a different
# document that no Desktop Icon ever referenced, so retiring the icon did nothing to it: it
# stayed public=1 / is_hidden=0 and kept reaching the browser through boot.workspaces.pages,
# which desk.js:305 assigns to frappe.boot.allowed_workspaces and then spreads into
# frappe.workspaces["erpnext-settings"] (routable at /app/erpnext-settings),
# frappe.modules[page.module], and frappe.visible_modules in breadcrumbs.js. It was the last
# "ERPNext" wording a FoLT user could actually see. Verified orphaned before hiding -- no
# Workspace Sidebar Item, Workspace Shortcut or child page links to it -- and "Setup" keeps a
# workspace in frappe.modules either way, because Home carries that module too.
HIDDEN_WORKSPACES = [
    "Manufacturing",
    "Selling",
    "Stock",
    "Projects",
    "Quality",
    "ERPNext Settings",
]


def hide_workspaces():
    """Remove the unused standard workspaces from the Desk sidebar.

    Sets public=0 and is_hidden=1 on each. Idempotent and safe to run on every migrate:
    only workspaces that exist and are not already in the target state are touched, so it
    is a no-op once applied. Re-runs after a migrate that re-syncs the upstream workspace
    (which would reset public=1), keeping the change durable.
    """
    changed = False
    for name in HIDDEN_WORKSPACES:
        if not frappe.db.exists("Workspace", name):
            continue
        current = frappe.db.get_value(
            "Workspace", name, ["public", "is_hidden"], as_dict=True
        )
        if current.public == 0 and current.is_hidden == 1:
            continue
        frappe.db.set_value(
            "Workspace", name, {"public": 0, "is_hidden": 1}, update_modified=False
        )
        changed = True
    if changed:
        frappe.clear_cache()


# Where a FoLT staff login lands. The route itself is the one on `add_to_apps_screen` in hooks.py
# (/desk/folt-tasks); this is the setting that makes frappe consult it.
#
# WHY THE HOOK ALONE IS NOT ENOUGH, because it looks as though it should be.
# frappe/apps.py:get_default_path() only returns an app's own route when the user has exactly one
# app on their apps screen:
#
#     if len(_apps) == 1:      return _apps[0].get("route") or "/desk"
#     elif is_desk_apps(_apps): return "/desk"          # <- this site takes this branch
#
# erpnext, hrms and folt_customizations all declare `add_to_apps_screen`, so `_apps` is three long
# and every route starts with /desk -- which lands every staff login on /desk, the apps-screen-ish
# page, no matter what FoLT's own route says. `default_app` is read *before* that branch and
# resolves through the same get_route(), so setting it is what makes the hook's route the answer.
#
# Set on System Settings rather than per User: it is where FoLT staff should start, not a personal
# preference, and `User.default_app` still overrides it for anyone who sets one (get_default_path
# checks the user's value first). Suppliers are unaffected -- get_apps() drops the FoLT tile for
# them via supplier_portal.desk_app_visible, and get_default_path returns None on an empty list
# before it ever reads this setting, so the portal home page still wins.
LANDING_APP = "folt_customizations"


def set_landing_page():
    """Send FoLT staff to My Tasks after login rather than to /desk.

    Idempotent and safe on every migrate: the value is compared first, so a second run is a
    no-op. In code rather than a Desk edit for the same reason the branding is -- a rebuilt
    container or a fresh site should come up landing in the right place.
    """
    if frappe.db.get_single_value("System Settings", "default_app") == LANDING_APP:
        return False

    frappe.db.set_single_value("System Settings", "default_app", LANDING_APP)
    frappe.clear_cache()
    return True


# The Workspace Sidebar definitions this app ships under folt_customizations/workspace_sidebar/.
# `bench migrate` does NOT import them: those files are *exports*, written out by
# WorkspaceSidebar.export_sidebar whenever the sidebar is edited in the Desk with developer_mode
# on, and nothing ever reads them back. So they look version-controlled while the live sidebar
# drifts from them -- adding an item to folt.json changed precisely nothing until this existed,
# and the doc in the database still carried its original 2026-07-28 items.
#
# Syncing them here makes the file on disk the source of truth it already pretends to be, and
# survives a rebuilt container or a fresh site, which is the same reason branding and the role
# permissions live in code.
SIDEBAR_DIR = "workspace_sidebar"

# The fields worth owning from disk. Deliberately not the whole document: `module` is derived in
# the doctype's own before_save, and the rest is bookkeeping.
SIDEBAR_FIELDS = ("header_icon",)

# One row of the `items` child table, in the order the JSON writes them.
ITEM_FIELDS = (
    "type", "label", "link_type", "link_to", "url", "icon", "child", "indent",
    "collapsible", "keep_closed", "show_arrow", "filters", "route_options",
)


def sync_workspace_sidebars():
    """Re-apply this app's shipped Workspace Sidebar definitions. True if anything changed."""
    import json
    import os

    import folt_customizations

    directory = os.path.join(os.path.dirname(folt_customizations.__file__), SIDEBAR_DIR)
    if not os.path.isdir(directory):
        return False

    changed = False
    for filename in sorted(os.listdir(directory)):
        if not filename.endswith(".json"):
            continue
        with open(os.path.join(directory, filename)) as handle:
            definition = json.load(handle)
        if _apply_sidebar(definition):
            changed = True

    if changed:
        frappe.clear_cache()
    return changed


def _apply_sidebar(definition):
    name = definition.get("name")
    if not name or not frappe.db.exists("Workspace Sidebar", name):
        # A sidebar this site has never had is left for a human: creating one blind would put an
        # unreviewed set of links in everybody's Desk.
        return False

    doc = frappe.get_doc("Workspace Sidebar", name)
    wanted = [
        {field: item.get(field) for field in ITEM_FIELDS} for item in definition.get("items") or []
    ]
    current = [{field: row.get(field) for field in ITEM_FIELDS} for row in doc.items]
    header_matches = all(
        doc.get(field) == definition.get(field) for field in SIDEBAR_FIELDS
    )
    if current == wanted and header_matches:
        return False

    for field in SIDEBAR_FIELDS:
        doc.set(field, definition.get(field))
    doc.set("items", [])
    for item in wanted:
        doc.append("items", item)
    doc.flags.ignore_permissions = True
    doc.save()
    return True
