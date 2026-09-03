import json

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

# The settings-flavoured variant of the tile above: FoLT's blue square with frappe's own
# gear glyph, so the FoLT Settings tile still reads as "settings" rather than as a second
# FoLT app. Solid, because System Settings.desktop_icon_style is "Solid" here and every
# neighbouring tile renders solid; the subtle twin ships alongside it for a site that flips
# that setting.
ICON_SETTINGS_SOLID = "/assets/folt_customizations/icons/desktop_icons/solid/folt_settings.svg"

# The loading mark for the login/boot wait. A separate file from EMBLEM on purpose: EMBLEM
# is also the favicon, and an animated favicon is both wrong and wasteful.
#
# It carries explicit width/height attributes, and that is load-bearing rather than
# cosmetic. frappe's splash is `.centered > img{width:auto;max-width:200px}` and `.centered`
# is `position:absolute` (global.scss:137), i.e. a shrink-to-fit box. An SVG with only a
# viewBox has no intrinsic width, contributes 0 to that box, and renders at **0x0** -- the
# splash was showing nothing at all. Measured in Chrome: with no width/height the image
# reports natural=115x150 but RENDERED=0x0, and frappe's own default splash logo behaves
# identically. Keep the width/height attributes on any asset used as splash_image.
SPLASH = "/assets/folt_customizations/images/folt-emblem-animated.svg"

# The email masthead logo, and the one asset here that is a PNG rather than an SVG. Mail
# clients are the reason: Gmail and Outlook both refuse to render `<img src="*.svg">`, so an
# SVG masthead is an alt-text placeholder in exactly the clients FoLT's suppliers and staff
# read mail in. frappe's own default (frappe-framework-logo.svg) has the same problem; it is
# just less visible because most sites never look at their own outgoing mail.
#
# Rendered from folt-logo.svg at 4x the 28px height frappe's email template hardcodes, on a
# transparent ground, with headless Chrome -- wkhtmltoimage (which the container does have)
# renders this file wrongly, collapsing the wordmark into an unreadable column. To regenerate
# after a logo change, on a machine with Chrome:
#
#   printf '%s' '<html><body style="margin:0"><img src="folt-logo.svg"
#     style="display:block;width:294px;height:112px"></body></html>' > wrap.html
#   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --disable-gpu \
#     --hide-scrollbars --default-background-color=00000000 --window-size=294,112 \
#     --screenshot=folt-logo-email.png wrap.html
LOGO_EMAIL = "/assets/folt_customizations/images/folt-logo-email.png"

# The letterhead lockup: the blue mark, the FoLT wordmark and the strapline, as supplied by
# FoLT. A different asset from LOGO on purpose -- LOGO is the horizontal wordmark the Desk
# navbar and login page need, this is the portrait block that belongs at the head of a printed
# document. Trimmed of its white border and reduced to a 128-colour palette (35 KB from 104 KB,
# no visible loss: the source is a screenshot, so it carries compression noise across thousands
# of near-identical colours).
#
# Supplied at 465x397, which is the ceiling on print quality here. If FoLT can produce the
# original vector or a larger export, replace the file -- nothing else has to change.
LETTERHEAD_IMAGE = "images/folt-letterhead.png"

# Rendered width of the letterhead, in CSS pixels. This artwork is nearly square and carries
# three lines of small type, so the width is a compromise rather than a house style: below about
# 150px the strapline stops being readable, and much above it the header eats into a payslip
# that is built to fit one A4 page (see print_format_templates/folt_salary_slip.html). 160px is
# ~42mm wide and ~36mm tall on the page, and keeps the slip to its single page -- verified, not
# assumed. Change it here and re-run apply_branding.
LETTERHEAD_WIDTH_PX = 160

# The wordmark's own blue, and the one colour every FoLT-built email uses for its accents --
# the masthead rule, section headings and the call-to-action button. Lives here rather than in
# either sending module so the committee email (notifications.py) and the supplier RFQ email
# (rfq_email.py) cannot drift apart. Note it is deliberately NOT the wordmark navy #001a33,
# which reads as near-black on a button.
EMAIL_ACCENT = "#3c6a91"

# The rest of the email palette, lifted from frappe's own email stylesheet so a FoLT-built email
# sits beside a frappe-built one without a seam: body text, secondary text, hairline rules.
EMAIL_TEXT = "#171717"
EMAIL_MUTED = "#6b7280"
EMAIL_RULE = "#ededed"

# The Letter Head record every printed document picks up. One default, no per-doctype variants.
LETTER_HEAD = "FoLT"

# ERPNext ships two sample letterheads and, worse, stamps one of them onto transactions through
# Company.default_letter_head -- so an existing Purchase Order or Salary Slip carries
# `letter_head = "Company Letterhead - Grey"` on the document itself, and printview prefers
# `doc.letter_head` over the default (frappe/www/printview.py:get_letter_head). Making FoLT's
# letterhead the default is therefore only a third of the job: the samples have to stop being
# offered, the Company has to stop stamping them, and the documents that already carry one have
# to be released back to the default. They are disabled rather than deleted -- reversible, and
# deleting a letterhead clears defaults that point at it.
SAMPLE_LETTER_HEADS = ("Company Letterhead", "Company Letterhead - Grey")

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
    "splash_image": SPLASH,
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
# "Framework" carries no Frappe wording, but it is developer-facing jargon for what is, to
# a FoLT user, the administration folder (System, Users, Email, Printing, Integrations,
# Website, Data, Automation, Build) -- so it is renamed to "System Admin". Renaming it to
# "FoLT" would NOT be safe: get_desktop_icon_by_label() resolves icons *by label* and that
# would collide with our own FoLT icon. "System Admin" is unique across the icon set.
#
# It is an icon_type "App" tile, so the label->Workspace Sidebar coupling above does not
# apply; what does apply is the parent_icon coupling -- its nine children are re-pointed by
# _reparent_desktop_icons() below, same as Frappe HR's. Its logo is replaced via
# DESKTOP_ICON_LOGOS (keyed by docname, which the rename leaves as "Framework"); after the
# rename the sidebar-header cascade no longer finds frappe's own framework.svg by label and
# falls through to that logo_url, which is what we want.
#
# Renaming the row is only half the job, and the other half is invisible: a user who has ever
# rearranged their /desk grid reads their labels out of a frozen per-user snapshot instead, and
# no rename applied here ever reaches them. See _refresh_saved_layouts().
DESKTOP_ICON_LABELS = {
    "Frappe HR": "FoLT HR",
    "ERPNext": "FoLT ERP",
    "Framework": "System Admin",
}

# "ERPNext Settings" is the one Link-type icon we do rename, and it takes an extra step
# because of the label->sidebar coupling described above. We ship our own Workspace Sidebar
# named "FoLT Settings" (workspace_sidebar/folt_settings.json, mirroring erpnext's
# ERPNext Settings items) so that workspace_sidebar_item["folt settings"] exists, then point
# both the label and link_to at it. erpnext's own sidebar doc is left untouched -- it simply
# ends up with no Desktop Icon referencing it, which makes it invisible rather than broken.
# That covers the Workspace *Sidebar* only: the Workspace of the same name is a separate
# document this rename cannot reach, and it is retired in workspaces.HIDDEN_WORKSPACES instead.
#
# Trade-off worth knowing: the item list in folt_settings.json is a snapshot taken from
# erpnext 16.30.0, so settings pages ERPNext adds later will not appear until it is
# re-synced by hand.
#
# logo_url has to carry the icon, and it is the one field that survives the rename: the
# filename route cannot serve this tile, because get_desktop_icon() builds the path from the
# icon's `app` (still "erpnext") plus frappe.scrub(label), and erpnext ships no
# folt_settings.svg. So the FoLT gear lives in *this* app's public/icons and is named
# explicitly here. `app` is deliberately left as "erpnext" -- it is what migrate re-syncs the
# row against, and changing it buys only style-following (solid/subtle) that a single
# explicit path already covers.
RELINKED_DESKTOP_ICONS = {
    "ERPNext Settings": {
        "label": "FoLT Settings",
        "link_to": "FoLT Settings",
        "logo_url": ICON_SETTINGS_SOLID,
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
    changed |= _refresh_saved_layouts()
    changed |= _hide_navbar_items()
    changed |= _apply_email_brand_logo()
    changed |= _apply_email_footer()
    changed |= _apply_letter_head()
    changed |= _retire_sample_letter_heads()
    if changed:
        frappe.clear_cache()


# The footer of every outgoing email. frappe assembles it from three sources (email_body.
# get_footer): the Email Account's own footer, the `email_footer_address` default, and the
# `default_mail_footer` hook -- and that last one is ERPNext's "Sent via ERPNext" promo, with a
# tracking link, at the bottom of every notification FoLT sends. A hook can only ADD to that
# list, never subtract, so the standard footer is switched off wholesale and FoLT's own line put
# in its place.
EMAIL_FOOTER = {
    "disable_standard_email_footer": 1,
    "email_footer_address": "Friends of Lake Turkana Trust",
}


def _apply_letter_head():
    """Put FoLT's letterhead at the head of every printed document and PDF.

    The image is embedded as a data URI rather than referenced at
    /assets/folt_customizations/images/folt-letterhead.png, and that is the whole point of
    this function rather than a two-line Desk edit.

    wkhtmltopdf runs inside the backend container with --disable-local-file-access and fetches
    every image over HTTP, resolving a relative src against the site's `host_name`. On this
    deployment host_name is what a browser needs (http://localhost:8080) and nothing listens on
    that port inside the container, so a URL-referenced letterhead renders as a broken image in
    every PDF -- and in production it would depend on the site being able to reach itself, which
    is a strange thing for a logo to depend on. A data URI needs no fetch at all, so the same
    bytes render in the Desk preview, in the PDF, and in a PDF built by a background job with no
    request context. The cost is ~46 KB of base64 in one field, paid once.

    `source = "HTML"` because the Image source path builds the same <img> from an attachment,
    which would mean a File record and an upload step outside version control.
    """
    content = (
        f'<div style="text-align:center;padding:4px 0">'
        f'<img src="{_letterhead_data_uri()}" alt="Friends of Lake Turkana"'
        f' style="width:{LETTERHEAD_WIDTH_PX}px;height:auto"></div>'
    )
    values = {
        "letter_head_name": LETTER_HEAD,
        "source": "HTML",
        "content": content,
        "align": "Center",
        "disabled": 0,
        "is_default": 1,
    }

    created = not frappe.db.exists("Letter Head", LETTER_HEAD)
    if created:
        doc = frappe.new_doc("Letter Head")
        doc.update(values)
        doc.insert(ignore_permissions=True)

    doc = frappe.get_doc("Letter Head", LETTER_HEAD)
    if all(doc.get(field) == value for field, value in values.items()):
        return created

    # The second write is not redundant on a fresh record. LetterHead.before_insert overrides
    # `source` to "Image" for anything not created during migrate or install -- a UX nicety for
    # someone uploading a logo in the Desk -- which leaves the record claiming to be
    # image-sourced while carrying HTML. Harmless until the next time anybody saves it in the
    # Desk: validate() would then rebuild `content` from the (empty) `image` field. Saving again
    # puts `source` back, because before_insert does not run on an update.
    doc.update(values)
    doc.save(ignore_permissions=True)
    return True


def _retire_sample_letter_heads():
    """Stop ERPNext's sample letterheads being offered, stamped or already stamped."""
    changed = False
    samples = [name for name in SAMPLE_LETTER_HEADS if frappe.db.exists("Letter Head", name)]

    for name in samples:
        current = frappe.db.get_value("Letter Head", name, ["disabled", "is_default"], as_dict=True)
        if current.disabled and not current.is_default:
            continue
        # db.set_value, not a save: LetterHead.validate rebuilds `content` from the `image`
        # field, and these samples are HTML-sourced -- saving them would blank their content on
        # the way to disabling them, which is a destructive way to turn something off.
        frappe.db.set_value(
            "Letter Head", name, {"disabled": 1, "is_default": 0}, update_modified=False
        )
        changed = True

    # New transactions get their letterhead from the Company, not from the Letter Head default.
    for company in frappe.get_all("Company", pluck="name"):
        if frappe.db.get_value("Company", company, "default_letter_head") != LETTER_HEAD:
            frappe.db.set_value(
                "Company", company, "default_letter_head", LETTER_HEAD, update_modified=False
            )
            changed = True

    if samples:
        changed |= _release_stamped_documents(samples)
    return changed


def _release_stamped_documents(samples):
    """Blank `letter_head` wherever it points at a retired sample, on every doctype that has one.

    Blanked rather than repointed at LETTER_HEAD on purpose: an empty field falls through to the
    default, so these documents follow whatever the default becomes instead of pinning today's
    answer into thousands of rows. Written with db.set_value because most of them are submitted
    and this is a print-only field -- no ledger, no workflow, nothing that validation guards.
    """
    changed = False
    doctypes = frappe.get_all(
        "DocField",
        filters={"fieldname": "letter_head", "fieldtype": "Link"},
        pluck="parent",
        distinct=True,
    )
    for doctype in doctypes:
        if not frappe.db.exists("DocType", doctype) or frappe.db.get_value(
            "DocType", doctype, "issingle"
        ):
            continue
        stale = frappe.get_all(doctype, filters={"letter_head": ["in", samples]}, pluck="name")
        for name in stale:
            frappe.db.set_value(doctype, name, "letter_head", None, update_modified=False)
        if stale:
            changed = True
    return changed


def _letterhead_data_uri():
    """The letterhead PNG as a data URI, read from the file shipped in this app."""
    import base64
    import os

    import folt_customizations

    path = os.path.join(
        os.path.dirname(folt_customizations.__file__), "public", LETTERHEAD_IMAGE
    )
    with open(path, "rb") as handle:
        encoded = base64.b64encode(handle.read()).decode()
    return f"data:image/png;base64,{encoded}"


def _apply_email_footer():
    """De-brand the outgoing email footer and sign it as FoLT.

    Both fields live on System Settings, but `get_footer` reads them from the DefaultValue
    table, not from the Single -- System Settings mirrors them there in its own `set_defaults()`
    on save. `_apply_single` writes with `set_single_value`, which skips that, so the mirror has
    to be done here or the setting reads as unset no matter what the form shows.
    """
    changed = _apply_single("System Settings", EMAIL_FOOTER)
    for fieldname, value in EMAIL_FOOTER.items():
        if frappe.db.get_default(fieldname) != str(value):
            frappe.db.set_default(fieldname, value)
            changed = True
    return changed


def _apply_email_brand_logo():
    """Put the FoLT logo in the masthead of every email the site sends.

    frappe renders that masthead from the outgoing Email Account's `brand_logo`, falling back
    to Website Settings `app_logo` -- which BRANDING above already sets, but to the SVG the
    Desk wants and mail clients will not display. Setting the account field overrides it for
    email only, so the Desk keeps the sharp vector and mail gets a PNG that actually renders.
    (Only shown on mail sent with `with_container` or a header -- see email_body.py.)

    Written with `db.set_value` rather than a document save on purpose: an Email Account save
    re-validates, and `validate_smtp_conn()` would have the site dial its own mail relay just
    to set a logo path -- which fails, loudly, on a machine that cannot reach it.
    """
    accounts = frappe.get_all(
        "Email Account", filters={"enable_outgoing": 1}, fields=["name", "brand_logo"]
    )
    stale = [account.name for account in accounts if account.brand_logo != LOGO_EMAIL]
    for name in stale:
        frappe.db.set_value("Email Account", name, "brand_logo", LOGO_EMAIL, update_modified=False)
    return bool(stale)


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


def _renamed_labels():
    """Desktop Icon docname -> the label FoLT gives it, for every icon we re-label.

    For a standard icon the docname IS the label the shipping app gave it, which is what makes
    this both the rename map and the old-label -> new-label map that the reparenting and the
    saved-layout rewrite below need. Both sources of renames are folded in, so nothing that
    renames an icon can be applied to the icon and forgotten everywhere else.
    """
    renames = dict(DESKTOP_ICON_LABELS)
    for name, values in RELINKED_DESKTOP_ICONS.items():
        if values.get("label"):
            renames[name] = values["label"]
    return renames


# The fields of a saved /desk layout row that belong to the Desktop Icon rather than to the
# arrangement. Everything else in the row -- idx, hidden, the folder an icon was dragged into --
# is the user's own decision and is left exactly as they left it.
LAYOUT_ICON_FIELDS = ("label", "logo_url", "link_to")


def _refresh_saved_layouts():
    """Re-apply the renames inside each user's saved /desk layout.

    WHY THIS IS NEEDED AT ALL, because renaming the Desktop Icon looks like the whole job.
    frappe v16 stores the /desk grid per user as a **Desktop Layout**: one JSON snapshot of the
    icon rows, written by desktop_layout.save_layout the first time that user drags a tile,
    creates a folder or hides one. desktop.py:17 hands it to the page and
    desktop.js:sync_layout() then prefers it outright:

        if (Object.keys(this.data).length != 0) frappe.desktop_icons = this.data;
        else                                    frappe.desktop_icons = frappe.boot.desktop_icons;

    So for anybody who has ever touched their layout, `frappe.boot.desktop_icons` -- the live
    rows _apply_desktop_icons() maintains -- is never read again. The snapshot carries its own
    `label`, and the tile renders `icon_data.label`, so the wording is frozen at whatever it was
    on the day that user rearranged their Desk. That is why staging kept showing "Framework" and
    "ERPNext Settings" long after the rename was applied, on a site whose Desktop Icon rows were
    correct all along: the labels were right, and nothing was reading them.

    It also freezes `parent_icon`, which is matched against the parent's *label* -- so a snapshot
    taken before a folder was renamed loses its children (desktop.js:prepare() pushes an icon
    whose `icon_map[parent_icon]` misses straight into the top-level grid). Remapping the old
    label to the new one puts them back in the folder.

    Deliberately a rewrite rather than a delete. Dropping the Desktop Layout row would also fix
    the wording -- the page would fall back to boot.desktop_icons -- and would throw away every
    arrangement decision the user has made: their tile order, their folders, the modules they
    hid. Only the fields that describe the *icon* are re-read from the live row; the arrangement
    is untouched.

    Scoped to the icons this module re-labels, so a layout is rewritten for FoLT's renames and
    for nothing else. Idempotent: a layout already in step is not written, so a second run is a
    no-op.
    """
    renames = _renamed_labels()
    live = {}
    for name in set(renames) | set(DESKTOP_ICON_LOGOS):
        row = frappe.db.get_value("Desktop Icon", name, LAYOUT_ICON_FIELDS, as_dict=True)
        if row:
            live[name] = row
    if not live:
        return False

    changed = False
    for name in frappe.get_all("Desktop Layout", pluck="name"):
        stored = frappe.db.get_value("Desktop Layout", name, "layout")
        if not stored:
            continue
        try:
            layout = json.loads(stored)
        except ValueError:
            # A layout we cannot read is a layout we must not overwrite: the user's arrangement
            # is in there, and replacing it with our own guess is worse than leaving the wording
            # stale. Their next rearrange rewrites it from the live rows anyway.
            continue
        if not isinstance(layout, list):
            continue
        if not _refresh_layout_icons(layout, live, renames):
            continue
        frappe.db.set_value(
            "Desktop Layout", name, "layout", json.dumps(layout), update_modified=False
        )
        changed = True

    return changed


def _refresh_layout_icons(icons, live, renames):
    """Re-read the icon fields of one saved layout from the live rows. True if anything moved.

    Recurses through `child_icons`, the copy of a folder's contents that the client writes back
    alongside the flat list. desktop.js:prepare() rebuilds that list from `parent_icon` on every
    render, so a stale nested copy changes nothing on screen -- it is rewritten so that the
    stored document does not disagree with itself, which is the sort of thing that costs an hour
    the next time somebody reads one of these by hand.
    """
    changed = False
    for icon in icons:
        if not isinstance(icon, dict):
            continue

        row = live.get(icon.get("name"))
        if row:
            for field in LAYOUT_ICON_FIELDS:
                # A None from the live row is "this icon has no such value" (link_to is NULL on
                # every App tile), not an instruction to clear what the layout holds.
                if row.get(field) is not None and icon.get(field) != row[field]:
                    icon[field] = row[field]
                    changed = True

        parent = renames.get(icon.get("parent_icon"))
        if parent and icon.get("parent_icon") != parent:
            icon["parent_icon"] = parent
            changed = True

        nested = icon.get("child_icons")
        if isinstance(nested, list) and _refresh_layout_icons(nested, live, renames):
            changed = True

    return changed


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
    for old_label, new_label in _renamed_labels().items():
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
