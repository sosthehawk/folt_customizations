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

# Enlarge + center the login-page logo (scoped to .for-login, so the Desk navbar is
# untouched). Served on website/login pages via the head include.
web_include_css = "/assets/folt_customizations/css/folt_branding.css"

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
add_to_apps_screen = [
    {
        "name": "folt_customizations",
        "logo": "/assets/folt_customizations/icons/desktop_icons/solid/folt.svg",
        "title": "FoLT",
        "route": "/desk/folt",
    }
]

# Replace the Frappe/ERPNext/Frappe HR app titles and logos in the boot payload. These
# drive the Desk sidebar header subtitle and the /apps screen, and they are unreachable
# from any hook we can declare -- see branding.rebrand_bootinfo for why.
extend_bootinfo = ["folt_customizations.branding.rebrand_bootinfo"]

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
    "folt_customizations.permissions.apply_role_permissions",
    "folt_customizations.access.apply_module_access",
    "folt_customizations.print_formats.apply_print_formats",
]
after_migrate = [
    "folt_customizations.branding.apply_branding",
    "folt_customizations.workspaces.hide_workspaces",
    "folt_customizations.workspaces.sync_workspace_sidebars",
    "folt_customizations.permissions.apply_role_permissions",
    "folt_customizations.access.apply_module_access",
    "folt_customizations.print_formats.apply_print_formats",
]

# A supplier pre-qualified for several FoLT categories carries the extras in the
# `folt_additional_supplier_groups` Table MultiSelect (Custom Field fixture). The hook
# keeps that table consistent with the primary `supplier_group` -- see supplier.py.
doc_events = {
    "Supplier": {
        "validate": "folt_customizations.supplier.validate",
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

# Called from frappe's `get_print` just before the PDF is built, and only on the PDF path.
# `host_name` is the base of every link the site emails to a person, so it has to be the
# browser's address; wkhtmltopdf needs one the container can reach. See print_formats.py.
on_print_pdf = "folt_customizations.print_formats.use_internal_host_for_pdf"

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
