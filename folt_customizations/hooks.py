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
website_context = {
    "favicon": "/assets/folt_customizations/images/folt-emblem.svg",
    "splash_image": "/assets/folt_customizations/images/folt-emblem-animated.svg",
    "brand_html": "<img src='/assets/folt_customizations/images/folt-logo.svg' alt='Friends of Lake Turkana' style='height:28px'>",
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
after_install = [
    "folt_customizations.branding.apply_branding",
    "folt_customizations.workspaces.hide_workspaces",
    "folt_customizations.permissions.apply_role_permissions",
]
after_migrate = [
    "folt_customizations.branding.apply_branding",
    "folt_customizations.workspaces.hide_workspaces",
    "folt_customizations.permissions.apply_role_permissions",
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
    # Notify the role that can make the next move whenever a document enters a state that
    # waits on somebody. Frappe writes one Workflow Action per transition, so hooking its
    # insert covers all eight FoLT workflows at once -- see notifications.py for why the
    # Desk bell is used rather than relying on the workflows' own email alert.
    "Workflow Action": {
        "after_insert": "folt_customizations.notifications.notify_pending_approvers",
    },
}

# Client-side half of the same rule: leads the form with the category and restricts the
# Supplier dropdown to that category's pre-qualified register.
doctype_js = {"Purchase Order": "public/js/purchase_order.js"}

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
]
FOLT_WORKFLOW_ACTIONS = [
    "Submit for Review", "Approve", "Reject", "Send to Committee",
    "Submit for Award Approval", "Approve (Intent to Award)", "Submit for Committee Review",
    "Submit for Finance Review", "Endorse", "Review & Forward", "Authorise", "Check",
    "Submit for approval", "Submit Payroll for Approval",
    "Submit for Verification", "Verify", "Return for Correction",
    "Mark Paid", "Mark Partly Paid", "Raise Dispute", "Resolve Dispute",
]

# Kenyan statutory payroll salary components (NSSF, SHIF, Housing Levy, PAYE + helpers).
FOLT_SALARY_COMPONENTS = [
    "Basic Salary", "NSSF", "SHIF", "Affordable Housing Levy", "Taxable Pay",
    "PAYE", "NSSF Employer", "Housing Levy Employer",
]
FOLT_SALARY_STRUCTURES = ["FoLT Kenya Payroll"]

# FoLT Supplier Groups acting as the pre-qualified supplier register (Section 4.1).
FOLT_SUPPLIER_GROUPS = ["Catering", "Car Hire", "Travel & Accommodation", "ICT"]

# Custom print formats matched to FoLT's existing paper forms.
FOLT_PRINT_FORMATS = [
    "FoLT Intent to Award",
    "FoLT Derogation Waiver Request",
    "FoLT Float Expense Report",
]

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
    {"doctype": "Print Format", "filters": [["name", "in", FOLT_PRINT_FORMATS]]},
]
