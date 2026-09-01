from json import dumps, loads

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
# THE SEED, not the standing list. This is the set a fresh install starts with; after that the
# Desk is the source of truth and hidden_workspaces() reads the store below. A System Manager who
# un-ticks Public on Assets, or ticks it on Projects, is making the decision -- not editing this.
SEED_HIDDEN_WORKSPACES = [
    "Manufacturing",
    "Selling",
    "Stock",
    "Projects",
    "Quality",
    "ERPNext Settings",
]

# Kept under the old name so nothing that imports it breaks; it is the seed either way.
HIDDEN_WORKSPACES = SEED_HIDDEN_WORKSPACES

# Where the standing decision lives. It cannot live in tabWorkspace, which is the whole reason
# this module exists: `bench migrate` re-imports every standard workspace from its shipping app
# and resets public=1, which is what hide_workspaces() undoes on the way out of a migrate. So the
# flags in the Desk are the *interface* and this is the *record* -- record_visibility_intent()
# writes it when somebody toggles a flag, and hide_workspaces() replays it after migrate has had
# its way with the table.
#
# frappe.db.set_global rather than a new Single doctype: it is one list of names, it is data
# rather than schema, and tabDefaultValue is not touched by migrate. A doctype would need a
# fixture, and a fixture of this would put the decision back in the repo -- the opposite of the
# point.
_STORE_KEY = "folt_hidden_workspaces"


def hidden_workspaces():
    """The modules currently retired from the Desk, as a set of Workspace names.

    Seeded from SEED_HIDDEN_WORKSPACES the first time it is asked for, so a fresh install gets
    FoLT's defaults and every install after that gets whatever its administrators have decided.
    An empty store is a legitimate answer (somebody re-published all six), which is why the seed
    fires on a *missing* key rather than on a falsy value.
    """
    stored = frappe.db.get_global(_STORE_KEY)
    if stored is None:
        _store(SEED_HIDDEN_WORKSPACES)
        return set(SEED_HIDDEN_WORKSPACES)
    return {name for name in loads(stored)} if stored else set()


def _store(names):
    frappe.db.set_global(_STORE_KEY, dumps(sorted(set(names))))


def hide_workspaces():
    """Re-apply the standing hidden set to the Desk sidebar.

    Sets public=0 and is_hidden=1 on each hidden workspace. Idempotent and safe to run on every
    migrate: only workspaces that exist and are not already in the target state are touched, so
    it is a no-op once applied. Re-runs after a migrate that re-syncs the upstream workspace
    (which would reset public=1), keeping the decision durable.

    One-directional on purpose. It hides what the store says to hide and never publishes anything
    -- a workspace dropped from the store needs no help, because the migrate that reset it to
    public=1 has already published it, and outside a migrate the Desk edit that dropped it from
    the store did. Nothing here should be able to make a workspace public that nobody asked to be.
    """
    changed = False
    for name in hidden_workspaces():
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


def record_visibility_intent(doc, method=None):
    """Workspace.on_update: make the Public / Hidden checkboxes mean what they look like.

    Before this, un-ticking Public in the Desk moved one flag on one row and nothing else. It
    dropped the workspace out of the sidebar page list, but the module's **icon** is a separate
    document that never reads that flag (see access.retired_modules), so the module stayed on
    screen and the edit read as having done nothing at all. Ticking Public back on was worse: the
    flag went to 1 and the next migrate put it back to 0, because the hidden set was a list in
    the repo that no Desk edit could reach.

    Both directions now run through here, and **the checkbox that changed is the decision**:

      Public changed      public=1 means show it, public=0 means take it away.
      only Hidden changed is_hidden=1 means take it away, is_hidden=0 means show it.

    Reading the two flags together instead -- hidden if `not public or is_hidden` -- is the
    obvious rule and it is wrong, in the one direction that matters. hide_workspaces() maintains
    the pair public=0 / is_hidden=1, so a module coming back out of retirement is always ticking
    Public while is_hidden is still 1: under the combined rule that reads as "still hidden", the
    normalisation below puts public straight back to 0, and the tick snaps back in the Desk. That
    is the same class of bug as the one this function exists to fix, so it is asserted in both
    directions by access_e2e.run_visibility_roundtrip.

    Whichever way the decision goes, the pair is then normalised -- public=0 / is_hidden=1 to hide,
    public=1 / is_hidden=0 to show -- so one tick does the whole job and the two flags cannot drift
    into the state where a module is gone for staff and still on screen for the administrator who
    hid it. (That is what is_hidden means on its own: frappe hides such a workspace from everyone
    *except* Workspace Managers.) Showing it also drops it from the store, so the next migrate
    leaves it published.

    Then access.apply_module_access() re-gates the module icon, which is what makes the change
    visible instead of theoretical.

    Private workspaces (`for_user`) are skipped: they are somebody's own page, they are already
    invisible to everyone else, and no module icon belongs to them.

    Written with db.set_value, so normalising the pair does not re-enter this hook -- and does
    not show up in the open form until it is reloaded.
    """
    if doc.for_user:
        return

    # Every other kind of save -- content blocks dragged about, a shortcut added, a rename --
    # leaves visibility alone and should not cost a permission sweep.
    if not (doc.has_value_changed("public") or doc.has_value_changed("is_hidden")):
        return

    # The flag that moved is the one being asked about. `public` wins when both moved: it is the
    # stronger of the two, since is_hidden still lets a Workspace Manager see the workspace.
    if doc.has_value_changed("public"):
        hidden = not doc.public
    else:
        hidden = bool(doc.is_hidden)
    target = {"public": 0, "is_hidden": 1} if hidden else {"public": 1, "is_hidden": 0}
    if (doc.public, doc.is_hidden) != (target["public"], target["is_hidden"]):
        frappe.db.set_value("Workspace", doc.name, target, update_modified=False)
        doc.public, doc.is_hidden = target["public"], target["is_hidden"]

    standing = hidden_workspaces()
    wanted = standing | {doc.name} if hidden else standing - {doc.name}
    if wanted != standing:
        _store(wanted)

    # Imported here rather than at module scope: access imports this module for the hidden set,
    # and at module scope that is a cycle.
    from folt_customizations.access import apply_module_access

    apply_module_access()
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
