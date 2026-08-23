import os

import frappe

# Print Formats whose templates FoLT maintains as files, applied on install and on every
# migrate. Each entry names the Print Format record and the doctype it prints; the Jinja
# template and its stylesheet are the same-named .html / .css under
# print_format_templates/.
#
# Why a hook and not a Print Format fixture, which is how the procurement forms used to
# ship: a payslip template is ~350 lines of Jinja plus ~150 lines
# of CSS, and a fixture stores both as single JSON string values. That is unreviewable in a
# diff and unpleasant to edit, and it splits the template across two places the moment
# someone tweaks the format in the Desk and forgets to re-export. Keeping the template in
# real files and upserting the record from them leaves exactly one source of truth, which
# is the same reasoning permissions.py gives for applying role permissions from a hook.
#
# `custom_format` is the setting that makes any of this take effect and it is easy to get
# wrong: with custom_format = 0, printview.get_rendered_template ignores `html` entirely
# and silently falls through to frappe's auto-generated "standard" layout, so the format
# appears in the print dropdown and prints something plausible that is not the template at
# all. The procurement and finance forms shipped that way as Print Format fixtures and were
# therefore printing the auto layout, not their own templates, for as long as they existed --
# which is why they are here now rather than in hooks.FOLT_PRINT_FORMATS.
#
# `stylesheet` names the .css file when it is not the template's own. The four form-shaped
# formats share folt_form.css and the page frame in _page_frame.html; the payslip keeps its
# own of both, because it is a one-page-at-all-costs layout whose rules make no sense on a
# form that is allowed to run long.
PRINT_FORMATS = {
    "FoLT Salary Slip": {
        "doc_type": "Salary Slip",
        "template": "folt_salary_slip",
        # The auto layout ran a single slip to three A4 pages; this one is built to hold a
        # month's earnings, deductions, statutory contributions and totals on one. Margins
        # are tighter than frappe's 15mm default to buy the room that needs.
        "margins": 12.0,
        # Both the Desk preview and the Print/PDF button resolve the format the same way
        # (printview.get_print_format_doc: ?format=, else meta.default_print_format, else
        # "Standard"), so this one Property Setter fixes both at once.
        "set_as_default": True,
    },
    # Step 4 of the finance workflow. Carries the three totals the paper form exists for --
    # total spent, float given, balance -- and enough rows that the margins are worth the
    # same tightening as the payslip.
    "FoLT Float Expense Report": {
        "doc_type": "Expense Claim",
        "template": "folt_float_expense_report",
        "stylesheet": "folt_form",
        "margins": 12.0,
        "set_as_default": True,
    },
    # Page one of every accountability pack: the authority to release the money. Made the
    # default for Payment Entry because at FoLT every payment is printed as a voucher and
    # signed -- pick another format from the print dropdown if a payment needs one.
    "FoLT Expense Voucher": {
        "doc_type": "Payment Entry",
        "template": "folt_expense_voucher",
        "stylesheet": "folt_form",
        "set_as_default": True,
    },
    "FoLT Intent to Award": {
        "doc_type": "Procurement Committee Evaluation",
        "template": "folt_intent_to_award",
        "stylesheet": "folt_form",
        "set_as_default": True,
    },
    "FoLT Derogation Waiver Request": {
        "doc_type": "Derogation Waiver Request",
        "template": "folt_derogation_waiver_request",
        "stylesheet": "folt_form",
        "set_as_default": True,
    },
}

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "print_format_templates")


def apply_print_formats():
    """Upsert FoLT's file-backed Print Formats and point their doctypes at them."""
    for name, spec in PRINT_FORMATS.items():
        _apply(name, spec)
        if spec.get("set_as_default"):
            _set_default_print_format(spec["doc_type"], name)


def _apply(name, spec):
    values = {
        "doc_type": spec["doc_type"],
        "module": "Folt Customizations",
        # "No" keeps the record editable without developer_mode (a "Yes" here makes
        # Print Format.validate throw on any save outside migrate), while custom_format
        # is what actually selects the template below over the auto layout.
        "standard": "No",
        "print_format_type": "Jinja",
        "custom_format": 1,
        "print_format_builder": 0,
        "print_format_for": "DocType",
        "disabled": 0,
        "pdf_generator": "wkhtmltopdf",
        "page_number": "Hide",
        "html": _read(spec["template"] + ".html"),
        "css": _read(spec.get("stylesheet", spec["template"]) + ".css"),
    }
    for side in ("top", "bottom", "left", "right"):
        values["margin_" + side] = spec.get("margins", 15.0)

    if frappe.db.exists("Print Format", name):
        doc = frappe.get_doc("Print Format", name)
        if all(doc.get(field) == value for field, value in values.items()):
            return False
    else:
        doc = frappe.new_doc("Print Format")
        doc.name = name

    doc.update(values)
    doc.flags.ignore_permissions = True
    doc.save()
    return True


def _set_default_print_format(doctype, print_format):
    """Make `print_format` the format the doctype opens with in preview and in PDF."""
    from frappe.custom.doctype.property_setter.property_setter import make_property_setter

    name = f"{doctype}-main-default_print_format"
    if frappe.db.exists("Property Setter", name):
        if frappe.db.get_value("Property Setter", name, "value") == print_format:
            return False
        frappe.db.set_value("Property Setter", name, "value", print_format)
    else:
        make_property_setter(
            doctype,
            "",
            "default_print_format",
            print_format,
            "Data",
            for_doctype=True,
            is_system_generated=False,
        )
    # Written after the fact rather than passed in: make_property_setter takes no module,
    # and the module is what puts the row in this app's Property Setter fixture filter.
    frappe.db.set_value("Property Setter", name, "module", "Folt Customizations")
    frappe.clear_cache(doctype=doctype)
    return True


def _read(filename):
    with open(os.path.join(TEMPLATE_DIR, filename)) as f:
        return f.read()
