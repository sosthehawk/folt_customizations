import functools
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

# Site config key naming a base URL that the *container* can reach, used only while building a
# PDF. See guard_pdf_host.
PDF_HOST_KEY = "pdf_host_name"

# Marks frappe's get_pdf as already wrapped, so the per-request/per-job installer is a no-op
# after the first call in a process.
_PDF_GUARD_FLAG = "_folt_pdf_host_guarded"


def guard_pdf_host(*args, **kwargs):
    """Bracket PDF generation so the container-internal host cannot escape into anything else.

    `host_name` has to be the address a *browser* uses, because it is also the base of every
    link and every image URL the site puts in an email or a notification -- and those are read
    by people, not by the container. On this deployment that is http://localhost:8080, which
    inside the backend container is the container itself with nothing listening on 8080.

    wkhtmltopdf does not care about any of that: it runs inside the container with
    --disable-local-file-access, and `get_pdf` opens with `scrub_urls(html)`, which resolves the
    printview page's <link> to frappe's compiled print stylesheet against host_name.
    Unreachable means wkhtmltopdf exits 1 with a network error, so the PDF does not build at all
    rather than merely printing unstyled. The letterhead sidesteps this by being a data URI (see
    branding._apply_letter_head); a 200 KB stylesheet emitted by frappe's own page template is
    not ours to inline, and pdf_body_html renders only the format body, so there is nothing to
    rewrite there.

    THIS REPLACES AN `on_print_pdf` HOOK THAT SET host_name AND NEVER PUT IT BACK. frappe offers
    no matching after-hook, and the docstring's claim that `frappe.local.conf` being per-request
    meant "nothing leaks past the response" was wrong in the way that mattered: the leak was
    never across requests, it was across the *rest of the same request or job*. Workflow action
    emails are built exactly that way --
    `workflow_action.get_common_email_args` calls `attach_print` and only then
    `frappe.sendmail`, so every such email was rendered with host_name still pointing at
    http://frontend:8080 and went out with
    `<img src="http://frontend:8080/assets/folt_customizations/images/folt-logo-email.png">` in
    its masthead: a hostname no mail client can resolve, so the FoLT logo was a broken image in
    every workflow notification the site sent. (The action buttons survived only by accident of
    ordering -- `get_users_next_action_data` builds those links *before* the attachment.)

    So the swap is scoped to the one call that needs it. `get_pdf` is imported inside the bodies
    of `get_print` and `attach_print`, i.e. looked up on the module at call time, so replacing
    the module attribute is picked up by both -- and by `attach_print(html=...)`, which calls
    `get_pdf` directly and which the old `on_print_pdf` hook did not cover at all.

    Installed once per process from this app's __init__.py -- the only entry point that runs in
    every context, including `bench execute` and plain scripts, which no hook covers. Idempotent,
    so a second call costs one getattr. A site with no `pdf_host_name` set is left completely
    alone: on staging and production the public hostname resolves from inside the container as
    well, and this problem only exists where the browser-facing address is loopback.

    Import failures are swallowed rather than raised: this runs at app import, so a broken
    optional dependency here would take the whole app down instead of degrading one PDF.
    """
    try:
        import frappe.utils.pdf as pdf_module
    except Exception:  # pragma: no cover - pdfkit/pypdf missing in a stripped environment
        return

    if getattr(pdf_module.get_pdf, _PDF_GUARD_FLAG, False):
        return

    original = pdf_module.get_pdf

    @functools.wraps(original)
    def get_pdf_on_internal_host(html, *args, **kwargs):
        internal_host = frappe.local.conf.get(PDF_HOST_KEY)
        if not internal_host:
            return original(html, *args, **kwargs)

        previous = frappe.local.conf.get("host_name")
        frappe.local.conf.host_name = internal_host
        try:
            return original(html, *args, **kwargs)
        finally:
            # Restored even when wkhtmltopdf raises -- a failed PDF must not leave the rest of
            # the request addressing a host that only exists inside the container.
            frappe.local.conf.host_name = previous

    setattr(get_pdf_on_internal_host, _PDF_GUARD_FLAG, True)
    pdf_module.get_pdf = get_pdf_on_internal_host


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
