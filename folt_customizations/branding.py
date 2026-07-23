import frappe

# FoLT brand assets, served from this app's public/ dir.
LOGO = "/assets/folt_customizations/images/folt-logo.svg"
EMBLEM = "/assets/folt_customizations/images/folt-emblem.svg"

# Website Settings fields -> asset. app_logo is the one that matters: get_app_logo()
# (frappe/core/doctype/navbar_settings) reads Website Settings.app_logo FIRST, and only
# falls back to the `app_logo_url` hook when exactly two apps define it — which breaks
# once erpnext + hrms + folt_customizations all define one. Setting it here sidesteps
# that entirely and covers both the login page and the Desk navbar.
BRANDING = {
    "app_name": "FoLT ERP",
    "app_logo": LOGO,
    "banner_image": LOGO,
    "favicon": EMBLEM,
    "splash_image": EMBLEM,
}


def apply_branding():
    """Point Website Settings at the FoLT logo/favicon assets.

    Idempotent and safe to run on every migrate. Kept in code (not a Desk edit) so a
    rebuilt container or a freshly created site comes up branded automatically.
    """
    current = (
        frappe.db.get_value(
            "Website Settings", "Website Settings", list(BRANDING.keys()), as_dict=True
        )
        or {}
    )
    to_set = {k: v for k, v in BRANDING.items() if current.get(k) != v}
    if not to_set:
        return
    for fieldname, value in to_set.items():
        frappe.db.set_single_value("Website Settings", fieldname, value)
    frappe.clear_cache()
