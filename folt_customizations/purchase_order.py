import frappe
from frappe import _
from frappe.utils import formatdate

from folt_customizations.supplier import get_supplier_groups, qualification_expiry

# FoLT procures by pre-qualified category, so the Purchase Order form leads with
# `folt_supplier_group` (Custom Field fixture) and treats `supplier` as the *outcome* of the
# competitive bidding rather than the starting point -- a Property Setter keeps `supplier`
# hidden until a category, or a derogation, is on the document.
#
# `supplier` itself stays: it is the accounting party ERPNext posts against, so it cannot be
# swapped out, only constrained. That constraint is enforced here as well as in the form
# script, because a link query only guards the dropdown, not the API.


def validate(doc, method=None):
    """Keep the awarded supplier inside the category the order was competed in.

    Runs on Purchase Order.validate via doc_events, after the controller's own validate:

      1. missing category is backfilled from the supplier's primary group, so orders raised
         by an API client, a background job or "Get Items From > Supplier Quotation" are
         never blocked by the new mandatory field;
      2. a supplier outside the chosen category is rejected -- unless a Derogation / Waiver
         Request is attached, since an approved waiver is precisely what licenses single
         sourcing outside the competitive process;
      3. a supplier whose pre-qualification has lapsed is rejected on a NEW order only.
         Qualification is a fact about the moment of award, and `folt_qualified_until` goes
         stale by the calendar rather than by anyone editing the document -- enforcing it on
         every save would strand an in-flight order mid-approval the day its supplier's
         registration expired, which is a filing problem, not grounds to void the award.
    """
    if not doc.supplier:
        return  # core mandatory validation reports this

    if not doc.get("folt_supplier_group"):
        doc.folt_supplier_group = frappe.db.get_value("Supplier", doc.supplier, "supplier_group")
        return

    if doc.get("folt_waiver_request"):
        return

    qualified_for = get_supplier_groups(doc.supplier)
    if doc.folt_supplier_group not in qualified_for:
        frappe.throw(
            _("{0} is not pre-qualified for {1}. Qualified categories: {2}.")
            .format(
                frappe.bold(doc.supplier_name or doc.supplier),
                frappe.bold(doc.folt_supplier_group),
                ", ".join(qualified_for) or _("none"),
            )
            + "<br><br>"
            + _("Award within the category, or link an approved Derogation / Waiver Request "
                "to single-source this order."),
            title=_("Supplier Not Pre-qualified"),
        )

    if doc.is_new():
        expired_on = qualification_expiry(doc.supplier)
        if expired_on:
            frappe.throw(
                _("{0}'s pre-qualification expired on {1}.").format(
                    frappe.bold(doc.supplier_name or doc.supplier),
                    frappe.bold(formatdate(expired_on)),
                )
                + "<br><br>"
                + _("Renew the supplier's Qualified Until date, award to another supplier in "
                    "{0}, or link an approved Derogation / Waiver Request.").format(
                        frappe.bold(doc.folt_supplier_group)
                    ),
                title=_("Pre-qualification Expired"),
            )
