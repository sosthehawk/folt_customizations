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

website_context = {
    "favicon": "/assets/folt_customizations/images/folt-emblem.svg",
    "splash_image": "/assets/folt_customizations/images/folt-emblem.svg",
    "brand_html": "<img src='/assets/folt_customizations/images/folt-logo.svg' alt='Friends of Lake Turkana' style='height:28px'>",
}

# app_logo_url alone isn't enough for the Desk navbar / login logo (see branding.py),
# so apply_branding() writes the logo into Website Settings on install and on every
# migrate — keeping the branding reproducible instead of a one-off Desk edit.
after_install = [
    "folt_customizations.branding.apply_branding",
    "folt_customizations.workspaces.hide_workspaces",
]
after_migrate = [
    "folt_customizations.branding.apply_branding",
    "folt_customizations.workspaces.hide_workspaces",
]

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
]
FOLT_WORKFLOW_ACTIONS = [
    "Submit for Review", "Approve", "Reject", "Send to Committee",
    "Submit for Award Approval", "Approve (Intent to Award)", "Submit for Committee Review",
    "Submit for Finance Review", "Endorse", "Review & Forward", "Authorise", "Check",
    "Submit for approval", "Submit Payroll for Approval",
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
