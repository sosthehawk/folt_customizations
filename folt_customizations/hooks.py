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
after_install = "folt_customizations.branding.apply_branding"
after_migrate = "folt_customizations.branding.apply_branding"

# Fixtures shipped with this app. `bench migrate` re-syncs these from disk into the
# database on every run -- so this file on disk is the source of truth. If you edit a
# Workflow, Custom Field or Property Setter in the Desk UI, re-export it here
# (`bench --site <site> export-fixtures`) before the next migrate, or the edit is lost.
fixtures = [
    "Accounting Dimension",
    {
        "doctype": "Role",
        "filters": [["name", "in", ["Finance Manager"]]],
    },
    {
        "doctype": "Workflow",
        "filters": [["name", "in", ["FoLT Purchase Order Approval"]]],
    },
    {
        "doctype": "Custom Field",
        "filters": [["module", "=", "Folt Customizations"]],
    },
    {
        "doctype": "Property Setter",
        "filters": [["module", "=", "Folt Customizations"]],
    },
]
