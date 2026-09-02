import frappe
from frappe import _
from frappe.model.workflow import get_workflow, get_workflow_name
from frappe.utils import formatdate, get_link_to_form

from folt_customizations.procurement import AUTHORISED, EVALUATION_DOCTYPE, WAIVER_DOCTYPE
from folt_customizations.supplier import get_supplier_groups, qualification_expiry

# FoLT procures by pre-qualified category, so the Purchase Order form leads with
# `folt_supplier_group` (Custom Field fixture) and treats `supplier` as the *outcome* of the
# competitive bidding rather than the starting point -- a Property Setter keeps `supplier`
# hidden until a category, or a derogation, is on the document.
#
# `supplier` itself stays: it is the accounting party ERPNext posts against, so it cannot be
# swapped out, only constrained. That constraint is enforced here as well as in the form
# script, because a link query only guards the dropdown, not the API.


# The two documents that can authorise an order, and what each one says about the supplier on it.
# Field name -> (doctype, the route it evidences, the field naming the supplier it authorises).
# Read by require_award_authority below; the hand-offs that create these links live in
# procurement_chain.py.
AUTHORITIES = {
    "folt_committee_evaluation": (EVALUATION_DOCTYPE, "competitive bidding", "recommended_supplier"),
    "folt_waiver_request": (WAIVER_DOCTYPE, "single sourcing", "supplier"),
}


def validate(doc, method=None):
    """Keep the awarded supplier inside the category the order was competed in.

    Runs on Purchase Order.validate via doc_events, after the controller's own validate:

      0. an order leaving Draft must carry the authority for it -- an approved committee award
         or an approved waiver. See require_award_authority; it is first because it is the one
         check that is about whether the order may exist at all rather than about its supplier,
         and it applies to an order with no supplier on it yet just the same;
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
    require_award_authority(doc)

    if not doc.supplier:
        return  # core mandatory validation reports this

    if not doc.get("folt_supplier_group"):
        doc.folt_supplier_group = frappe.db.get_value("Supplier", doc.supplier, "supplier_group")
        return

    # An approved waiver is what licenses buying outside the pre-qualified register; a waiver
    # somebody has merely typed is not, and until now any link in this field -- draft, rejected,
    # or a form opened five minutes ago -- was enough to skip the rest of this function.
    # require_award_authority has already refused the order if the link is not approved, so this
    # only has to ask whether it is the state that grants the exemption.
    if _authorised(doc, "folt_waiver_request"):
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


def require_award_authority(doc):
    """Refuse to let an order leave Draft without the document that authorises it.

    FoLT procures two ways and both of them end in a decision somebody signs: the Procurement
    Committee's award, approved by the Head of Finance, or -- where competing the purchase is
    being waived -- a Derogation / Waiver Request authorised by the Executive Director. Until
    this ran, neither was needed. A buyer could raise an order for any pre-qualified supplier and
    send it to the Finance Manager for approval with no competition and no waiver behind it, and
    the order was indistinguishable from one that had both: `folt_waiver_request` was an optional
    audit link, and there was no field at all for the award.

    THE GATE IS AT THE EDGE OF DRAFT, not at insert and not only at submit, and the middle one is
    the point. Draft is where an order is assembled -- from a quotation, by hand, by an amendment
    -- and demanding the authority before the lines exist would make the hand-offs in
    procurement_chain impossible to write. But `Submit for approval` is the moment the order
    stops being the buyer's own working copy and becomes something a Finance Manager is asked to
    approve, and asking them to approve a commitment with no award behind it is exactly the
    failure this closes. So the rule is: prepare it freely, authorise it before you pass it on.

    What "authorised" means is checked against the linked document itself rather than trusted
    from the link: submitted, and at the state its own workflow calls Approved. A waiver in
    `Pending Executive Director Approval` is a case being made, not a decision.
    """
    if _being_prepared(doc):
        return

    linked = {field: doc.get(field) for field in AUTHORITIES if doc.get(field)}

    if not linked:
        frappe.throw(
            _("{0} has nothing authorising it, so it cannot be sent for approval.").format(
                frappe.bold(doc.name or _("This order"))
            )
            + "<br><br>"
            + _("Link either an approved <b>{0}</b> (competitive bidding) or an approved "
                "<b>{1}</b> (single sourcing). Both can be raised from the supplier quotation "
                "the purchase is based on; a waiver can also be raised on its own.").format(
                    _(EVALUATION_DOCTYPE), _(WAIVER_DOCTYPE)
                ),
            title=_("Order Not Authorised"),
        )

    for field, name in linked.items():
        doctype, route, supplier_field = AUTHORITIES[field]
        authority = frappe.db.get_value(
            doctype, name, ["workflow_state", "docstatus", supplier_field], as_dict=True
        )
        if not authority:
            continue  # a dangling link; frappe's own link validation reports it

        if authority.docstatus != 1 or authority.workflow_state != AUTHORISED:
            frappe.throw(
                _("{0} is at {1} and has not been approved, so it authorises nothing yet.").format(
                    get_link_to_form(doctype, name),
                    frappe.bold(_(authority.workflow_state or _("Draft"))),
                )
                + "<br><br>"
                + _("An order is issued on an approved decision. Take {0} through its own "
                    "approval first, or clear the link.").format(frappe.bold(name)),
                title=_("Authority Not Approved"),
            )

        awarded = authority.get(supplier_field)
        if awarded and doc.supplier and awarded != doc.supplier:
            frappe.throw(
                _("{0} authorises {1} by {2}, not {3}.").format(
                    get_link_to_form(doctype, name),
                    frappe.bold(awarded),
                    _(route),
                    frappe.bold(doc.supplier_name or doc.supplier),
                )
                + "<br><br>"
                + _("Order from the supplier the decision names, or take the change back through "
                    "the decision -- an award is for one supplier at one price."),
                title=_("Not the Awarded Supplier"),
            )


def _authorised(doc, field: str) -> bool:
    """Whether `field` names a decision that has actually been approved.

    Its own query rather than a value carried over from require_award_authority, because it is
    asked in a different circumstance: the order may still be in Draft, where the gate above
    deliberately stays out of the way, and a draft waiver must not exempt a draft order from the
    pre-qualification rule any more than an approved one exempts it from being competed.
    """
    name = doc.get(field)
    if not name:
        return False

    doctype = AUTHORITIES[field][0]
    state, docstatus = frappe.db.get_value(doctype, name, ["workflow_state", "docstatus"]) or (None, None)

    return docstatus == 1 and state == AUTHORISED


def _being_prepared(doc) -> bool:
    """Whether this order is still the buyer's own draft.

    Derived from the workflow rather than from the string "Draft": FoLT's Purchase Order workflow
    is a fixture that `bench migrate` re-imports, and a site that renames its first state or
    inserts a step before `Pending Approval` should not quietly lose the gate.

    An order on a site with no Purchase Order workflow at all is "being prepared" only while it
    is unsubmitted -- there is no edge of Draft to put the gate at, so it falls back to the
    submit itself, which is the last moment at which the rule can still be enforced.
    """
    if doc.docstatus != 0:
        return False

    if not get_workflow_name(doc.doctype):
        return True

    workflow = get_workflow(doc.doctype)
    state = doc.get(workflow.workflow_state_field)

    return not state or state == workflow.states[0].state
