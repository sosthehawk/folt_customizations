import frappe

# Competitive bidding at FoLT ends in a Procurement Committee Evaluation, and what the committee
# actually evaluates is the set of Supplier Quotations that came back against one Request for
# Quotation. Reading that set is not a one-liner (see rfq_quotations below), and both the form
# script and the evaluation's own validate() need it, so it lives here rather than in either.

EVALUATION_DOCTYPE = "Procurement Committee Evaluation"

# The other route to an order: single sourcing, licensed by an approved waiver rather than by a
# competition. Named alongside the evaluation because the two are alternatives -- procurement_chain
# offers both out of a submitted bid, and purchase_order accepts an order backed by either.
WAIVER_DOCTYPE = "Derogation Waiver Request"

# The evaluation workflow state in which the committee does its scoring. Named here rather
# than in the doctype controller because notifications.py has to recognise it too, and the
# controller already imports this module -- naming it there would make that import circular.
COMMITTEE_REVIEW_STATE = "Committee Reviewing"

# The state both routes end at, and the only one an order may be issued on. One name for both
# workflows because it is one fact. purchase_order.require_award_authority reads it from here
# rather than from procurement_chain, which imports activity_chain and its doctype controllers:
# every Purchase Order save runs that check, and it has no business loading the float chain to
# do it.
AUTHORISED = "Approved"


@frappe.whitelist()
def get_rfq_quotations(request_for_quotation: str | None = None) -> list[dict]:
    """Quotations received against an RFQ, for the evaluation form to fill its scoring grid.

    Permission-checked explicitly because `rfq_quotations` deliberately is not: this returns
    every bidder's price for one competition, which is exactly the information the RFQ process
    exists to keep between the buyer and the committee until an award is recommended. Write on
    the evaluation is the right gate -- Purchase User, Procurement Committee and Head of Finance
    all have it (see the doctype's .json), and nobody else has any business filling in a grid.
    """
    frappe.has_permission(EVALUATION_DOCTYPE, ptype="write", throw=True)
    return rfq_quotations(request_for_quotation)


def rfq_quotations(request_for_quotation: str | None) -> list[dict]:
    """The Supplier Quotations submitted against `request_for_quotation`, cheapest first.

    Two things about this query are not obvious:

    The RFQ link lives on Supplier Quotation *Item*, not on the quotation itself, so the set has
    to be read through the child table and then de-duplicated -- a supplier who quotes three RFQ
    lines has still submitted one bid, and the committee scores bids, not lines.

    Drafts are included (`docstatus < 2`). A quotation keyed in by the buyer, or arriving through
    the supplier portal, sits at draft until somebody submits it, and a bid the committee cannot
    see is a bid it cannot score -- a silently missing bidder is a worse failure here than an
    unsubmitted one appearing. Cancelled quotations are out.

    Note `frappe.get_all` ignores permissions by design; that is wanted here (the rows become
    part of the evaluation document, and its own validate() re-derives them for whoever saves
    it) and is why the whitelisted wrapper above does the permission check itself.
    """
    if not request_for_quotation:
        return []

    names = frappe.get_all(
        "Supplier Quotation Item",
        filters={"request_for_quotation": request_for_quotation, "docstatus": ["<", 2]},
        pluck="parent",
        distinct=True,
    )
    if not names:
        return []

    return frappe.get_all(
        "Supplier Quotation",
        filters={"name": ["in", names], "docstatus": ["<", 2]},
        fields=[
            "name as supplier_quotation",
            "supplier",
            "grand_total",
            "currency",
            "valid_till",
        ],
        order_by="grand_total asc, name asc",
    )


def withdraw_cancelled_quotation(doc, method=None):
    """Take a cancelled bid out of the evaluations that are still scoring it.

    Hooked on Supplier Quotation.on_cancel. Without this a withdrawn bid freezes every
    evaluation that has it in the grid, because `Document._save` validates links *before* it
    runs `validate()` (frappe/model/document.py: `_validate_links()` then
    `run_before_save_methods()`). So `sync_quotation_scores` -- which would happily drop the row
    -- never gets a turn: the save dies on a cancelled link, in a read-only cell the committee
    cannot clear by hand.

    Pruning the row in memory and then saving is what breaks the deadlock: the link check passes,
    `validate()` re-derives the grid, and the parent's timestamp moves so anyone with the form
    already open is made to reload rather than saving the stale row straight back.

    Only draft evaluations are touched. A submitted one records a decision already taken, and it
    has to keep the bids that decision was taken on.
    """
    stale = frappe.get_all(
        "Procurement Committee Quotation Score",
        filters={
            "supplier_quotation": doc.name,
            "parenttype": EVALUATION_DOCTYPE,
            "docstatus": 0,
        },
        pluck="parent",
        distinct=True,
    )
    if not stale:
        return

    for name in stale:
        evaluation = frappe.get_doc(EVALUATION_DOCTYPE, name)
        evaluation.set(
            "quotation_scores",
            [row for row in evaluation.quotation_scores if row.supplier_quotation != doc.name],
        )
        evaluation.save(ignore_permissions=True)

    frappe.msgprint(
        frappe._("Withdrawn from {0} committee evaluation(s) still in progress: {1}").format(
            len(stale), ", ".join(sorted(stale))
        ),
        indicator="orange",
    )
