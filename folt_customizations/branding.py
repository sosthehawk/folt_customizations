import frappe

# FoLT brand assets, served from this app's public/ dir.
LOGO = "/assets/folt_customizations/images/folt-logo.svg"
EMBLEM = "/assets/folt_customizations/images/folt-emblem.svg"

# The Desk renders logos inside a fixed 32x32 box and forces the image to fill it
# (`.header-logo img { width:100%; height:100% }` in frappe's sidebar_header.scss), so a
# non-square asset gets squashed. folt-emblem.svg is portrait (viewBox -16 -8 328 429),
# and folt-logo.svg is a 2.6:1 wordmark -- neither survives that box. The desktop-icon
# tiles below are square by construction (54x54, frappe's own icon convention), so they
# are what every Desk-side logo field points at.
ICON_SOLID = "/assets/folt_customizations/icons/desktop_icons/solid/folt.svg"

# What the product is called once Frappe/ERPNext branding is replaced.
#   Frappe Framework -> FoLT      ERPNext -> FoLT ERP      Frappe HR -> FoLT HR
APP_NAME = "FoLT ERP"

# Website Settings fields -> value. app_logo is the one that matters for the login page:
# get_app_logo() (frappe/core/doctype/navbar_settings) reads Website Settings.app_logo
# FIRST, and only falls back to the `app_logo_url` hook when that list has exactly two
# entries -- which stopped being true once frappe + erpnext + hrms + folt_customizations
# all defined one (it resolves to a 4-item list, so logos[0], frappe's, would win).
# Setting it here sidesteps that entirely.
#
# footer_powered replaces frappe's "Built on Frappe" footer include: footer_info.html
# prefers this field over templates/includes/footer/footer_powered.html when it is set.
BRANDING = {
    "app_name": APP_NAME,
    "app_logo": LOGO,
    "banner_image": LOGO,
    "favicon": EMBLEM,
    "splash_image": EMBLEM,
    "footer_powered": "Friends of Lake Turkana",
}

# System Settings fields -> value. These are the Frappe/ERPNext strings and promos that
# no hook can reach:
#   app_name                        fallback app name for www/desk.py and www/login.py
#   otp_issuer_name                 shipped as "Frappe Framework"; shows in the user's
#                                   authenticator app entry and in OTP emails
#   disable_product_suggestion      suppresses the "Switch to Frappe CRM" / "Switch to
#                                   Helpdesk" promo banners frappe renders in the Desk
#                                   sidebar for System Managers
#   disable_standard_email_footer   suppresses "Sent via ERPNext", which erpnext appends
#                                   to every outgoing mail via the `default_mail_footer`
#                                   hook. That hook is a list and is additive across apps,
#                                   so there is no way to override it from here -- this
#                                   wholesale switch is the only lever. Note it also
#                                   suppresses any footer added the same way; if a FoLT
#                                   footer line is wanted later, set this back to 0 and
#                                   use Email Account -> footer instead.
SYSTEM_BRANDING = {
    "app_name": APP_NAME,
    "otp_issuer_name": APP_NAME,
    "disable_product_suggestion": 1,
    "disable_standard_email_footer": 1,
}

# The logo in the top-left of /desk -- the landing page a user hits straight after login.
# That page is not the workspace sidebar; it renders its own navbar, and desk/page/desktop/
# desktop.py reads Navbar Settings.app_logo, falling back to frappe's own logo hook when
# the field is empty. It was empty, which is why /desk showed the Frappe mark.
NAVBAR_BRANDING = {
    "app_logo": ICON_SOLID,
}

# Desk app tiles (Desktop Icon, icon_type "App") that ship Frappe/ERPNext wording.
# Rename the `label` only -- `name` is what other records link to.
#
# ONLY icon_type "App" icons are safe to rename. For an icon_type "Link" icon whose
# link_type is "Workspace Sidebar", the label is load-bearing for navigation: both
# desktop.js:get_route() and utils.js:get_route_for_icon() resolve the target with
# `frappe.boot.workspace_sidebar_item[desktop_icon.label.toLowerCase()]`, and boot.py:450
# keys that dict by the Workspace Sidebar's `name`. Rename the label and the lookup misses,
# `route` stays undefined and the tile becomes a dead link that msgprints "Icon is not
# correctly configured". That is why "ERPNext Settings" is not in this map -- see the note
# in the module docstring of this section below.
#
# "Framework" is deliberately absent for a different reason: its label carries no Frappe
# wording, and renaming it to "FoLT" would collide with our own FoLT icon, since
# get_desktop_icon_by_label() resolves icons *by label*. Its Frappe wordmark logo is still
# replaced, via DESKTOP_ICON_LOGOS below.
DESKTOP_ICON_LABELS = {
    "Frappe HR": "FoLT HR",
    "ERPNext": "FoLT ERP",
}

# "ERPNext Settings" is the one Link-type icon we do rename, and it takes an extra step
# because of the label->sidebar coupling described above. We ship our own Workspace Sidebar
# named "FoLT Settings" (workspace_sidebar/folt_settings.json, mirroring erpnext's
# ERPNext Settings items) so that workspace_sidebar_item["folt settings"] exists, then point
# both the label and link_to at it. erpnext's own sidebar doc is left untouched -- it simply
# ends up with no Desktop Icon referencing it, which makes it invisible rather than broken.
#
# Trade-off worth knowing: the item list in folt_settings.json is a snapshot taken from
# erpnext 16.30.0, so settings pages ERPNext adds later will not appear until it is
# re-synced by hand.
#
# logo_url reuses erpnext's existing gear asset so the tile looks exactly as it did. The
# filename route cannot serve it: get_desktop_icon() builds the path from the icon's `app`
# (still "erpnext") plus frappe.scrub(label), and there is no erpnext_settings ->
# folt_settings.svg to find.
RELINKED_DESKTOP_ICONS = {
    "ERPNext Settings": {
        "label": "FoLT Settings",
        "link_to": "FoLT Settings",
        "logo_url": "/assets/erpnext/icons/desktop_icons/subtle/erpnext_settings.svg",
    },
}

# Desktop Icon -> logo_url. Two jobs here:
#
# 1. The top-left mark in the Desk. Frappe v16 has no navbar brand at all (the old
#    .navbar-brand is dead code); the top-left slot is the Workspace Sidebar header, whose
#    icon cascade in sidebar_header.js:set_header_icon() is:
#       1. assets/<app>/icons/desktop_icons/solid/<scrub(label)>.svg
#       2. desktop_icon.logo_url
#       3. a *generated grey letter tile*   <- what FoLT got before this
#       4. boot.app_data[0].app_logo_url
#    "FoLT" is covered by step 1 (public/icons/desktop_icons/solid/folt.svg, matched on
#    frappe.scrub("FoLT") == "folt"). "Procurement & Finance" can never be: step 1 needs
#    `app` set on the icon and that one has none, and scrubbing its label would put an "&"
#    in a filename. So it needs logo_url. FoLT is listed too, harmless and explicit.
#
# 2. The app tiles on /desk, which ship Frappe and ERPNext wordmark logos.
DESKTOP_ICON_LOGOS = {
    "FoLT": ICON_SOLID,
    "Procurement & Finance": ICON_SOLID,
    "Framework": ICON_SOLID,
    "Frappe HR": ICON_SOLID,
    "ERPNext": ICON_SOLID,
}

# Help-dropdown entries frappe seeds from its `standard_help_items` hook. They are re-synced
# into Navbar Settings on every migrate by navbar_settings.sync_standard_items(), and
# sync_table() only drops items whose label disappeared from the hook -- so hiding one
# sticks, provided we re-apply it *after* the sync. after_migrate guarantees that ordering.
HIDDEN_NAVBAR_ITEMS = ["Frappe Support"]

# boot.app_data[*].app_title, per app. This cannot be done with a hook: boot.py builds each
# entry from `add_to_apps_screen[0].title` or the `app_title` hook read with
# app_name=<that app>, so an app only ever sets its own. hrms hardcodes "Frappe HR" in both.
# See rebrand_bootinfo().
# The sidebar header renders "<workspace> / <app title>", so folt_customizations is
# "FoLT ERP" rather than "FoLT" -- otherwise the FoLT workspace reads "FoLT / FoLT".
APP_TITLES = {
    "frappe": "FoLT",
    "erpnext": "FoLT ERP",
    "hrms": "FoLT HR",
    "folt_customizations": "FoLT ERP",
}


def apply_branding():
    """Replace Frappe/ERPNext branding with FoLT's across the Desk, login page and emails.

    Idempotent and safe to run on every migrate: every write is compared against the
    current value first, so a second run is a no-op and the cache is left alone. Kept in
    code (not a Desk edit) so a rebuilt container or a freshly created site comes up
    branded automatically -- and, for the Desktop Icon and Navbar Item rows, because
    migrate re-syncs those from the shipping apps and would otherwise undo the change.
    """
    changed = False
    changed |= _apply_single("Website Settings", BRANDING)
    changed |= _apply_single("System Settings", SYSTEM_BRANDING)
    changed |= _apply_single("Navbar Settings", NAVBAR_BRANDING)
    changed |= _apply_desktop_icons()
    changed |= _hide_navbar_items()
    if changed:
        frappe.clear_cache()


def _apply_single(doctype, values):
    """Set fields on a Single, skipping any already at the target value."""
    current = (
        frappe.db.get_value(doctype, doctype, list(values.keys()), as_dict=True) or {}
    )
    to_set = {k: v for k, v in values.items() if current.get(k) != v}
    for fieldname, value in to_set.items():
        frappe.db.set_single_value(doctype, fieldname, value)
    return bool(to_set)


def _apply_desktop_icons():
    """Re-label and re-logo the Desk icons that carry Frappe/ERPNext branding."""
    updates = {}
    for name, label in DESKTOP_ICON_LABELS.items():
        updates.setdefault(name, {})["label"] = label
    for name, logo_url in DESKTOP_ICON_LOGOS.items():
        updates.setdefault(name, {})["logo_url"] = logo_url
    for name, values in RELINKED_DESKTOP_ICONS.items():
        updates.setdefault(name, {}).update(values)

    changed = False
    for name, values in updates.items():
        if not frappe.db.exists("Desktop Icon", name):
            continue
        current = frappe.db.get_value(
            "Desktop Icon", name, list(values.keys()), as_dict=True
        )
        to_set = {k: v for k, v in values.items() if current.get(k) != v}
        if not to_set:
            continue
        frappe.db.set_value("Desktop Icon", name, to_set, update_modified=False)
        changed = True
    return changed | _reparent_desktop_icons()


def _reparent_desktop_icons():
    """Re-point child icons at their parent's new label after a rename.

    An "App" tile groups its workspaces into a folder, and the grouping is keyed by
    *label*, not by name: children carry `parent_icon = "<parent label>"`, and
    sidebar_header.js:build_folder_map()/desktop.js match that against the parent icon's
    label. So renaming "Frappe HR" -> "FoLT HR" on its own silently orphans all nine HR
    workspaces -- the folder reports 0 workspaces and the children scatter across the
    /desk grid as loose tiles. frappe does this reparenting itself when a folder is
    renamed in the UI (desktop.js:add_icons_to_folder); we have to do it here because we
    rename in the database.

    Idempotent: once the children point at the new label there is nothing left to match,
    and migrate resets both fields together so the pair is always re-applied as a unit.
    """
    changed = False
    for old_label, new_label in DESKTOP_ICON_LABELS.items():
        for child in frappe.get_all(
            "Desktop Icon", filters={"parent_icon": old_label}, pluck="name"
        ):
            frappe.db.set_value(
                "Desktop Icon", child, "parent_icon", new_label, update_modified=False
            )
            changed = True
    return changed


def _hide_navbar_items():
    """Hide the Frappe-branded rows in the Desk help dropdown."""
    changed = False
    for label in HIDDEN_NAVBAR_ITEMS:
        for row in frappe.get_all(
            "Navbar Item",
            filters={"parent": "Navbar Settings", "item_label": label, "hidden": 0},
            pluck="name",
        ):
            frappe.db.set_value("Navbar Item", row, "hidden", 1, update_modified=False)
            changed = True
    return changed


def rebrand_bootinfo(bootinfo):
    """Rewrite the per-app titles and logos the Desk sidebar reads out of the boot payload.

    Wired up as an `extend_bootinfo` hook, so it runs after frappe has assembled
    bootinfo.app_data. This is the only place these can be changed: boot.py resolves each
    entry from that app's own `add_to_apps_screen` title or `app_title` hook, both read
    with app_name pinned to the app being described, so no hook we declare can reach
    hrms's "Frappe HR" or erpnext's "ERPNext". The Desk sidebar header subtitle and the
    /apps screen both render these values.
    """
    for app in bootinfo.get("app_data") or []:
        title = APP_TITLES.get(app.get("app_name"))
        if title:
            app["app_title"] = title
        app["app_logo_url"] = ICON_SOLID
