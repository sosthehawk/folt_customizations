app_name = "folt_customizations"
app_title = "FoLT Customizations"
app_publisher = "Friends of Lake Turkana"
app_description = "Shared dimension model, workflows and donor reporting customizations for FoLT's ERPNext deployment"
app_email = "ict@folt.org"
app_license = "MIT"

# --- FoLT branding ---------------------------------------------------------
# Served from this app's public/ dir at /assets/folt_customizations/... . Setting
# these here (rather than in the Desk UI) keeps the branding version-controlled and
# baked into the image, so every rebuilt container comes up already branded.
# app_logo_url drives the Desk navbar and login-page logo; website_context covers
# the public website brand, favicon and the loading splash.
app_logo_url = "/assets/folt_customizations/images/folt-logo.svg"

# The website/portal half of the stylesheet set. Three files, and the order is load-bearing:
#
#   folt_theme.css   the SAME file the Desk loads (see app_include_css below). Every token it
#                    sets -- --primary, --primary-color, --btn-primary, --heading-color,
#                    --focus-default, --font-stack -- is declared in website.bundle.css too,
#                    with exactly one definition each, and the website has NO dark theme
#                    (templates/base.html emits no data-theme attribute), so its dark block is
#                    inert here and its light block simply applies. Loading it on both surfaces
#                    is what makes the supplier portal and the Desk the same product rather
#                    than two things that happen to share a logo -- and it declares Lexend
#                    once for both.
#   folt_portal.css  the supplier-portal components (the RFQ pricing page) plus a short,
#                    enumerated set of rules for frappe's portal chrome.
#   folt_branding.css  the logo rules. LAST on purpose: its own comment says it is the last
#                    stylesheet the website loads, which is why its navbar rule needs no
#                    !important. Keep it there so that stays true.
web_include_css = [
    "/assets/folt_customizations/css/folt_theme.css",
    "/assets/folt_customizations/css/folt_portal.css",
    "/assets/folt_customizations/css/folt_branding.css",
]

# Same job for outgoing email: frappe inlines every `email_css` file into the message body
# (premailer, via email_body.inline_style_in_html), which is the only way to reach the markup
# in frappe's own standard.html email template. A plain path rather than a `.bundle.css` one
# on purpose -- `bundled_asset` passes anything without ".bundle." through untouched, so this
# needs no build step, and premailer reads it off disk through the sites/assets symlink.
email_css = ["/assets/folt_customizations/css/folt_email.css"]

# Without this, frappe's boot.py falls through to the raw `app_logo_url` hook list and
# app_data["folt_customizations"].app_logo_url ends up a *list* where every other app's is
# a string -- it only survives because JS coerces a one-element array to its element.
# Declaring the app properly also gives FoLT a correct tile on the /apps screen. The logo
# is the square desktop-icon tile, not the wordmark: the Desk renders these in a 32x32 box.
#
# `has_permission` is not decoration: frappe answers "where do I send this user after login"
# with the route of the only app on their apps screen (frappe/apps.py:get_default_path), and
# with FoLT as the only entry that was sending *suppliers* into a Desk workspace they cannot
# open. Hiding the tile from portal users is what lets the login reach get_home_page() and the
# supplier portal -- see supplier_portal.desk_app_visible.
# `route` is also where a FoLT staff login lands -- but NOT on the strength of this hook alone.
# get_default_path() only returns an app's own route when the user has exactly one app on their
# apps screen, and this site has three (erpnext, hrms, folt_customizations), so it falls through
# to a bare "/desk". System Settings.default_app is what makes frappe consult this route, and
# workspaces.set_landing_page sets it -- the full reasoning is there. Both halves are needed: the
# setting names the app, this names the page inside it.
add_to_apps_screen = [
    {
        "name": "folt_customizations",
        "logo": "/assets/folt_customizations/icons/desktop_icons/solid/folt.svg",
        "title": "FoLT",
        "route": "/desk/folt-tasks",
        "has_permission": "folt_customizations.supplier_portal.desk_app_visible",
    }
]

# Deep links into the Vue app at /folt. The bare path resolves by filename (www/folt.html), but
# frappe's router 404s everything below it unless a rule says otherwise -- so /folt/<anything> is
# mapped onto the same document and the tail arrives as frappe.form_dict.app_path for the SPA
# router to pick up. This is the app's first website_route_rules entry. See www/folt.py.
website_route_rules = [
    {"from_route": "/folt/<path:app_path>", "to_route": "folt"},
]

# Where a supplier login lands. Called for every user logging in; it answers only for portal-only
# supplier accounts and hands everybody else back to frappe -- see supplier_portal for why this
# is a hook and not Role["Supplier"].home_page.
get_website_user_home_page = "folt_customizations.supplier_portal.portal_home_page"

# Replace the Frappe/ERPNext/Frappe HR app titles and logos in the boot payload. These
# drive the Desk sidebar header subtitle and the /apps screen, and they are unreachable
# from any hook we can declare -- see branding.rebrand_bootinfo for why.
# add_turn_downs_to_bootinfo hands the Desk the list of workflow actions that turn a document
# down, derived from the workflows themselves, so folt_workflow.js can ask for a reason before
# the action runs rather than after the server has refused it. See workflow_access.
# add_guide_to_bootinfo hands the Desk the shape of every active workflow -- the steps in order,
# whose step each one is, and which of them are also steps in FoLT's six-document Finance SOP --
# so folt_guide.js can put "step 3 of 6, waiting for the Finance Officer, next raise the
# reimbursement list" on the document itself instead of leaving it in the SOP. It replaces the
# older add_chain_to_bootinfo, which carried a subset of the same fact. See document_guide.
extend_bootinfo = [
    "folt_customizations.branding.rebrand_bootinfo",
    "folt_customizations.workflow_access.add_turn_downs_to_bootinfo",
    "folt_customizations.document_guide.add_guide_to_bootinfo",
]

# Plain path rather than a `.bundle.js` one, for the same reason email_css is: anything without
# ".bundle." is passed through untouched, so this needs no build step.
app_include_js = [
    "/assets/folt_customizations/js/folt_workflow.js",
    "/assets/folt_customizations/js/folt_guide.js",
]

# web_include_css above reaches the *website* only -- which is why folt_branding.css has never had
# any effect on the Desk, as its own comments say. The Desk is a separate template:
# frappe/www/desk.py collects the `app_include_css` hook and desk.html emits it in <head>.
# folt_customizations is last in get_installed_apps(), so this is the last stylesheet the Desk
# loads and an equal-specificity rule wins on order -- there is no !important in the file.
# Plain path again, so no build step. Everything in it is scoped to `.folt-` classes this app
# generates, so it cannot reach a form, a list or the print preview. See folt_desk.css.
#
# TWO files, and the ORDER MATTERS -- though not for the reason it usually does.
# folt_theme.css restyles frappe's own chrome (typeface, primary colour, heading colour, focus
# ring); folt_desk.css never selects a frappe node. Those are contradictory contracts, so they
# cannot live in one file: see the header of each. Listing the theme FIRST keeps the sentence
# four lines above literally true -- folt_desk.css remains the last stylesheet the Desk loads,
# which is the whole of why it needs no !important. The theme wins its own fights on
# specificity, so it loses nothing by going first.
# (A bare string was already being listified -- frappe.append_hook does
# `if not isinstance(value, list): value = [value]` -- so this is not a behaviour change.)
app_include_css = [
    "/assets/folt_customizations/css/folt_theme.css",
    "/assets/folt_customizations/css/folt_desk.css",
]

# splash_image is the animated mark, not the plain emblem: it is what shows during the
# login wait, and it needs intrinsic width/height to render at all inside frappe's
# shrink-to-fit `.centered` splash box. See branding.SPLASH. favicon stays static.
# brand_html carries its height inline rather than leaving it to folt_branding.css, and that
# is deliberate: folt-logo.svg declares only a viewBox, so an <img> with no height at all
# falls back to CSS's default 300x150 sizing -- a stylesheet that fails to load would blow
# the logo up to ten times its intended size instead of merely mis-sizing it. The inline
# value is the target size; the CSS only has to clear frappe's cap (see folt_branding.css).
website_context = {
    "favicon": "/assets/folt_customizations/images/folt-emblem.svg",
    "splash_image": "/assets/folt_customizations/images/folt-emblem-animated.svg",
    "brand_html": "<img src='/assets/folt_customizations/images/folt-logo.svg' alt='Friends of Lake Turkana' style='height:40px'>",
}

# app_logo_url alone isn't enough for the Desk navbar / login logo (see branding.py),
# so apply_branding() writes the logo into Website Settings on install and on every
# migrate — keeping the branding reproducible instead of a one-off Desk edit. It also
# de-brands System Settings, the help dropdown and the Desk app tiles; several of those
# rows are re-synced from frappe/erpnext/hrms by migrate itself, so re-applying *after*
# migrate is what makes the change durable rather than a one-off.
# apply_role_permissions() grants FoLT's custom roles the permissions their workflow steps
# need on standard doctypes (Purchase Order, Employee Advance, Salary Slip). It runs here
# rather than as a Custom DocPerm fixture so the grants are additive and idempotent -- see
# permissions.py for why a fixture would be the wrong tool.
# apply_module_access() is the other half of that: permissions decide what a role can open,
# and this decides which module icons and workspaces it is offered at all. Both surfaces
# default to "visible to everyone" and are re-synced from the shipping apps by migrate, which
# is why this runs after it rather than being a fixture -- see access.py.
# apply_print_formats() upserts the Print Formats whose Jinja templates FoLT keeps as files
# under print_format_templates/ (currently the salary slip) and points their doctype's
# preview and PDF at them. Same reasoning: a fixture would bury a 350-line template in a
# JSON string -- see print_formats.py.
after_install = [
    "folt_customizations.branding.apply_branding",
    "folt_customizations.workspaces.hide_workspaces",
    "folt_customizations.workspaces.sync_workspace_sidebars",
    "folt_customizations.workspaces.set_landing_page",
    "folt_customizations.permissions.apply_role_permissions",
    "folt_customizations.access.apply_module_access",
    "folt_customizations.print_formats.apply_print_formats",
    "folt_customizations.supplier_portal.link_portal_users",
]
after_migrate = [
    "folt_customizations.branding.apply_branding",
    "folt_customizations.workspaces.hide_workspaces",
    "folt_customizations.workspaces.sync_workspace_sidebars",
    "folt_customizations.workspaces.set_landing_page",
    "folt_customizations.permissions.apply_role_permissions",
    "folt_customizations.access.apply_module_access",
    "folt_customizations.print_formats.apply_print_formats",
    "folt_customizations.supplier_portal.link_portal_users",
]

# A supplier pre-qualified for several FoLT categories carries the extras in the
# `folt_additional_supplier_groups` Table MultiSelect (Custom Field fixture). The hook
# keeps that table consistent with the primary `supplier_group` -- see supplier.py.
doc_events = {
    # Every doctype, because the rule is about workflows rather than about any one document, and
    # it bails out on a cached lookup for the doctypes that have none. Both events are needed and
    # only one runs per save: Frappe skips `validate` when a submitted document is edited and
    # calls `before_update_after_submit` instead, which is where half of FoLT's approval states
    # live. See workflow_access -- `allow_edit` is a Desk-only convention until this runs.
    #
    # A rejection is a step in the chain aimed at a named person, so it carries the reason they
    # need -- required on the way down, cleared on the way back out, and written into the timeline
    # once the save has stuck. This is FoLT's answer to "how does a document get turned down"; a
    # cancellation, which says nothing to anybody, is not it.
    "*": {
        "validate": [
            "folt_customizations.workflow_access.enforce_state_custodian",
            "folt_customizations.workflow_access.require_rejection_reason",
        ],
        "before_update_after_submit": [
            "folt_customizations.workflow_access.enforce_state_custodian",
            "folt_customizations.workflow_access.require_rejection_reason",
        ],
        "on_update": "folt_customizations.workflow_access.record_rejection_reason",
        "on_update_after_submit": "folt_customizations.workflow_access.record_rejection_reason",
    },
    "Supplier": {
        "validate": "folt_customizations.supplier.validate",
    },
    # erpnext only ever adds a supplier's login to Supplier.portal_users when an RFQ is emailed
    # to that exact address, and that table is the *only* thing the portal reads to decide which
    # supplier a visitor is. Linking a contact to a supplier here is what makes the portal work
    # for a login created any other way -- see supplier_portal.
    "Contact": {
        "on_update": "folt_customizations.supplier_portal.sync_portal_user",
    },
    # FoLT competes every order inside a pre-qualified category, so a Purchase Order carries
    # `folt_supplier_group` and its `supplier` has to be qualified for it -- enforced here as
    # well as in the form script, since a link query only guards the dropdown. See
    # purchase_order.py.
    "Purchase Order": {
        "validate": "folt_customizations.purchase_order.validate",
    },
    # A Procurement Committee Evaluation scores the bids received against one RFQ, and a bid
    # can be withdrawn while the committee is still scoring it. Frappe validates links before
    # it runs validate(), so a cancelled quotation left in the grid would freeze the evaluation
    # outright -- see procurement.withdraw_cancelled_quotation.
    "Supplier Quotation": {
        "on_cancel": "folt_customizations.procurement.withdraw_cancelled_quotation",
    },
    # Notify the role that can make the next move whenever a document enters a state that
    # waits on somebody. Frappe writes one Workflow Action per transition, so hooking its
    # insert covers every FoLT workflow at once -- see notifications.py for why the
    # Desk bell is used rather than relying on the workflows' own email alert.
    "Workflow Action": {
        "after_insert": "folt_customizations.notifications.notify_pending_approvers",
    },
    # Steps 3 and 4 of FoLT's finance workflow -- disbursement, then accountability -- happen
    # on documents *other* than the float: a Payment Entry funds it, an Expense Claim retires
    # it. ERPNext already recomputes Employee Advance.status from those vouchers, so the float's
    # workflow state is derived from them rather than clicked a second time. See
    # float_lifecycle.py for why the sync is deferred to after the commit.
    "Employee Advance": {
        "validate": "folt_customizations.float_lifecycle.set_retirement_deadline",
    },
    "Payment Entry": {
        "on_submit": "folt_customizations.float_lifecycle.sync_from_voucher",
        "on_cancel": "folt_customizations.float_lifecycle.sync_from_voucher",
    },
    "Journal Entry": {
        "on_submit": "folt_customizations.float_lifecycle.sync_from_voucher",
        "on_cancel": "folt_customizations.float_lifecycle.sync_from_voucher",
    },
    "Expense Claim": {
        "on_submit": "folt_customizations.float_lifecycle.sync_from_claim",
        "on_cancel": "folt_customizations.float_lifecycle.sync_from_claim",
    },
}

# The other half of the Float Request Form's own undertaking: a float unaccounted for more than
# three days after the activity stops looking current. The sweep flags; recovery from salary
# stays a human decision -- see float_lifecycle.flag_overdue_floats.
scheduler_events = {
    "daily": [
        "folt_customizations.float_lifecycle.flag_overdue_floats",
    ],
}

# ERPNext mails a Request for Quotation to its suppliers as the bare `Message for Supplier`
# field: no FoLT mark, no statement of what is being asked for, and no link to the portal where
# the quotation is actually entered. The subclass wraps that message in a branded frame carrying
# the portal button and, for a supplier with no login yet, a set-password link. Nothing else on
# the doctype is touched -- see rfq_email.py for why this is an override rather than a template.
override_doctype_class = {
    "Request for Quotation": "folt_customizations.rfq_email.FoLTRequestForQuotation",
    # frappe's "your password has been changed" alert is two sentences of bare text with no FoLT
    # mark and none of the facts a security notice needs -- which account, when, by whom. The
    # subclass replaces just that notification; the password change itself is still upstream's.
    # See user_security.py.
    "User": "folt_customizations.user_security.FoLTUser",
}

# Client-side half of the same rule: leads the form with the category and restricts the
# Supplier dropdown to that category's pre-qualified register.
doctype_js = {"Purchase Order": "public/js/purchase_order.js"}

# `host_name` is the base of every link AND every image URL the site emails to a person, so it
# has to be the browser's address; wkhtmltopdf, running inside the container, needs one the
# container can reach. The guard swaps the second in for the duration of `get_pdf` and puts the
# first back in a `finally`. It replaces an `on_print_pdf` hook that set host_name and never
# restored it, which left every workflow action email -- built by attaching a print and only
# then sending -- with a masthead logo pointing at a host no mail client can resolve.
# Installed once per process from this app's __init__.py, which is the only entry point that runs
# in every context -- web request, background job, bench execute and plain script. See
# print_formats.guard_pdf_host, and folt_customizations/__init__.py for why not a hook.

# Fixtures shipped with this app. `bench migrate` re-syncs these from disk into the
# database on every run -- so this file on disk is the source of truth. If you edit a
# Workflow, Custom Field or Property Setter in the Desk UI, re-export it here
# (`bench --site <site> export-fixtures`) before the next migrate, or the edit is lost.
# Custom roles required by FoLT's approval chain (Section 7 of the Implementation Guide).
FOLT_ROLES = [
    "Finance Manager",
    "Head of Programs",
    "Head of Finance",
    "Procurement Committee",
    "Finance Officer",
    "Executive Director",
    "Finance Assistant",
    "Operations Support Officer",
]

# Workflows attached to the procurement / finance / payroll doctypes.
FOLT_WORKFLOWS = [
    "FoLT Purchase Order Approval",
    "Activity Requisition Approval",
    "Procurement Committee Evaluation Approval",
    "Derogation Waiver Request Approval",
    "Employee Advance Float Approval",
    "FoLT Payroll Approval",
    "Activity Participant List Verification",
    "Participant Reimbursement List Verification",
    "FoLT Float Retirement Approval",
]

# Workflow State / Action masters referenced by the workflows above. ERPNext 16 validates
# that these master records exist before a Workflow can be imported, so they must ship as
# fixtures too (otherwise `bench migrate` fails on a fresh build with a LinkValidationError).
FOLT_WORKFLOW_STATES = [
    "Draft", "Pending Head of Programs", "Pending Head of Finance", "Committee Reviewing",
    "Pending Head of Finance Approval", "Pending Committee Review",
    "Pending Finance Officer Review", "Pending Executive Director Approval",
    "Requested", "Checked", "Approved", "Rejected", "Pending Approval",
    "Pending Payroll Approval",
    "Pending Verification", "Verified", "Paid", "Partly Paid", "Disputed",
    # The float's life after the approval decision (float_lifecycle.py), and the retirement
    # claim's own settlement state.
    "Disbursed", "Overdue", "Accounted", "Closed", "Settled",
]
FOLT_WORKFLOW_ACTIONS = [
    "Submit for Review", "Approve", "Reject", "Send to Committee",
    "Submit for Award Approval", "Approve (Intent to Award)", "Submit for Committee Review",
    "Submit for Finance Review", "Endorse", "Review & Forward", "Authorise", "Check",
    "Submit for approval", "Submit Payroll for Approval",
    "Submit for Verification", "Verify", "Return for Correction",
    "Mark Paid", "Mark Partly Paid", "Raise Dispute", "Resolve Dispute",
    "Record Disbursement", "Mark Accounted", "Flag Overdue", "Close Float", "Mark Settled",
]

# Kenyan statutory payroll salary components (NSSF, SHIF, Housing Levy, PAYE + helpers).
FOLT_SALARY_COMPONENTS = [
    "Basic Salary", "NSSF", "SHIF", "Affordable Housing Levy", "Taxable Pay",
    "PAYE", "NSSF Employer", "Housing Levy Employer",
]
FOLT_SALARY_STRUCTURES = ["FoLT Kenya Payroll"]

# FoLT Supplier Groups acting as the pre-qualified supplier register (Section 4.1).
FOLT_SUPPLIER_GROUPS = ["Catering", "Car Hire", "Travel & Accommodation", "ICT"]

# There is deliberately no Print Format fixture. Every FoLT print format is now file-backed --
# template and stylesheet under print_format_templates/, upserted by the apply_print_formats
# hook above -- so that the template has exactly one source of truth. Shipping them as fixtures
# instead stored each template as a single JSON string, which no diff could review, and it hid
# the bug that mattered: all three carried custom_format = 0 and were quietly printing frappe's
# auto layout rather than their own markup. See print_formats.py.

# Order matters: masters and Custom Fields (e.g. the Employee Advance `workflow_state` field)
# must import before the Workflows that reference them, so a fresh `bench migrate` on an empty
# database does not fail with a LinkValidationError.
fixtures = [
    "Accounting Dimension",
    {"doctype": "Role", "filters": [["name", "in", FOLT_ROLES]]},
    {"doctype": "Custom Field", "filters": [["module", "=", "Folt Customizations"]]},
    {"doctype": "Property Setter", "filters": [["module", "=", "Folt Customizations"]]},
    {"doctype": "Supplier Group", "filters": [["name", "in", FOLT_SUPPLIER_GROUPS]]},
    {"doctype": "Salary Component", "filters": [["name", "in", FOLT_SALARY_COMPONENTS]]},
    {"doctype": "Workflow State", "filters": [["name", "in", FOLT_WORKFLOW_STATES]]},
    {"doctype": "Workflow Action Master", "filters": [["name", "in", FOLT_WORKFLOW_ACTIONS]]},
    {"doctype": "Salary Structure", "filters": [["name", "in", FOLT_SALARY_STRUCTURES]]},
    {"doctype": "Workflow", "filters": [["name", "in", FOLT_WORKFLOWS]]},
]
