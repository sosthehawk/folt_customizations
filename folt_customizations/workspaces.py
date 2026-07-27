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
