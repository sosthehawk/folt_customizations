import frappe

# Standard ERPNext workspaces FoLT does not use, hidden from the Desk sidebar.
# Kept in code (not a one-off Desk edit, and not a Workspace JSON fixture) so we set
# only the `is_hidden` flag and leave the rest of each upstream workspace definition
# untouched -- a Workspace fixture would overwrite the whole doc and mask ERPNext's
# own updates to these workspaces on version upgrades.
HIDDEN_WORKSPACES = [
    "Manufacturing",
    "Selling",
    "Stock",
    "Projects",
    "Quality",
]


def hide_workspaces():
    """Set is_hidden=1 on the unused standard workspaces.

    Idempotent and safe to run on every migrate. Only touches workspaces that both
    exist and are not already hidden, so it is a no-op once applied.
    """
    changed = False
    for name in HIDDEN_WORKSPACES:
        if not frappe.db.exists("Workspace", name):
            continue
        if frappe.db.get_value("Workspace", name, "is_hidden"):
            continue
        frappe.db.set_value("Workspace", name, "is_hidden", 1)
        changed = True
    if changed:
        frappe.clear_cache()
