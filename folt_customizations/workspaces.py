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
HIDDEN_WORKSPACES = [
    "Manufacturing",
    "Selling",
    "Stock",
    "Projects",
    "Quality",
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
