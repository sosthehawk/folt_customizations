"""The procurement chain: how a submitted bid becomes an authorised order.

FoLT buys two ways, and the Implementation Guide (sections 4 and 5) is explicit that they are
two routes to the same place rather than two systems:

    competitive bidding   Activity Requisition -> Request for Quotation -> Supplier Quotation(s)
                          -> Procurement Committee Evaluation ("Intent to Award")
                          -> Purchase Order
    single sourcing       Supplier Quotation (sole bid) or no quotation at all
                          -> Derogation / Waiver Request
                          -> Purchase Order

EVERY DOCUMENT ON BOTH ROUTES ALREADY EXISTED, AND THE JOINS BETWEEN THEM DID NOT. A submitted
Supplier Quotation was the end of the line: nothing on it said which route the purchase was
taking, nothing raised the evaluation or the waiver from it, and the Purchase Order at the far
end would accept any pre-qualified supplier with no award and no waiver behind it. So the two
approval gates FoLT's procurement policy rests on -- the committee's recommendation and the
Executive Director's authorisation of an exception to it -- were documents somebody had to
remember to open, and an order raised without either was indistinguishable from one raised with
both.

This module is the join, and `purchase_order.require_award_authority` is the gate. Together they
are one rule: an order is issued on the strength of a competition or of an approved exception to
one, and never on nothing.

The two rules of `activity_chain` hold here too, and for the same reasons:

*A hand-off copies facts, it does not invent them.* The evaluation gets the RFQ and its
requester; the waiver gets the supplier, the value quoted and the lines quoted for. What is
genuinely new at the next step -- who sits on the committee, why the competition is being waived
-- is left empty, and the created document says so on arrival.

*A hand-off is recorded on the document it creates.* `Procurement Committee
Evaluation.request_for_quotation`, `Derogation Waiver Request.supplier_quotation`, and on the
order itself `folt_committee_evaluation` / `folt_waiver_request`. Those four links are what make
the route auditable afterwards, and they are what the gate on the order reads.

WHY THE HAND-OFF MACHINERY IS NOT SHARED WITH activity_chain. It nearly is -- `Handoff` itself
is imported from there rather than restated -- but readiness asks a different question on this
chain. A Supplier Quotation has no workflow at all: its step is its docstatus, and erpnext's own
submit is what advances it. And both of the routes out of a bid are prepared by a role that is
not necessarily the one holding the bid (the Operations Support Officer prepares waivers, the
buyer prepares evaluations), so readiness here also asks whether the person reading the form may
create the thing the button offers -- a button that always throws is worse than no button.
"""

import frappe
from frappe import _
from frappe.model.workflow import get_workflow, get_workflow_name
from frappe.utils import flt, fmt_money, formatdate, get_link_to_form, nowdate

from folt_customizations.activity_chain import Handoff
from folt_customizations.procurement import (
	AUTHORISED,
	EVALUATION_DOCTYPE,
	WAIVER_DOCTYPE,
	rfq_quotations,
)

# `AUTHORISED` -- the state both routes end at -- is the readiness test for the two hand-offs into
# the Purchase Order below, and the gate in purchase_order.py tests the same state. Both read it
# from procurement.py; see the note there.
#
# A Supplier Quotation is not workflow-governed -- erpnext submits it, from the buyer's form or
# from the supplier portal -- so these two stand in for the states it does not have. See _state.
SUBMITTED = "Submitted"
UNSUBMITTED = "Draft"


HANDOFFS: dict[str, list[Handoff]] = {
	"Supplier Quotation": [
		Handoff(
			label="Committee Evaluation",
			target=EVALUATION_DOCTYPE,
			method="make_committee_evaluation",
			arg="supplier_quotation",
			ready=(SUBMITTED,),
			link_field="request_for_quotation",
			# One competition, one evaluation -- and the link is the RFQ, not this bid, which is
			# why `exclusive` alone cannot express it. See _evaluation_of.
			exclusive=True,
			description="The committee scores every bid received against this RFQ and recommends an award.",
		),
		Handoff(
			label="Derogation / Waiver Request",
			target=WAIVER_DOCTYPE,
			method="make_waiver_request",
			arg="supplier_quotation",
			ready=(SUBMITTED,),
			link_field="supplier_quotation",
			exclusive=True,
			description="Single sourcing: the case for buying from this supplier without competing it.",
		),
	],
	EVALUATION_DOCTYPE: [
		Handoff(
			label="Purchase Order",
			target="Purchase Order",
			method="make_award_order",
			arg="committee_evaluation",
			ready=(AUTHORISED,),
			link_field="folt_committee_evaluation",
			# An award is ordered once. A second order off the same evaluation is a second
			# commitment against one competition, and nothing downstream would notice.
			exclusive=True,
			description="The order for the winning bid, on the strength of the committee's award.",
		),
	],
	WAIVER_DOCTYPE: [
		Handoff(
			label="Purchase Order",
			target="Purchase Order",
			method="make_waiver_order",
			arg="derogation_waiver_request",
			ready=(AUTHORISED,),
			link_field="folt_waiver_request",
			exclusive=True,
			description="The single-source order this waiver authorises.",
		),
	],
}


# --- a submitted bid -> the committee that scores it ----------------------------------------


@frappe.whitelist()
def make_committee_evaluation(supplier_quotation: str) -> dict:
	"""Open the evaluation for the competition this bid belongs to.

	Raised from a bid rather than from the RFQ because the bid is what a buyer has in front of
	them when the last quotation comes back -- but what it creates belongs to the *competition*:
	one evaluation per RFQ, scoring every bid received. `sync_quotation_scores` on the evaluation
	fills the grid from the RFQ, so a bid that arrives after this was raised still gets scored.

	The committee itself is not filled in. Who sits on it is chosen per RFQ, deliberately, to be
	random and to exclude the requester (the evaluation's own `enforce_conflict_of_interest`), and
	there is nothing on the bid to copy it from.
	"""
	quotation = _ready_source("Supplier Quotation", supplier_quotation, EVALUATION_DOCTYPE)
	rfq = _competition(quotation)

	existing = _evaluation_of(rfq)
	if existing:
		frappe.throw(
			_("{0} is already being evaluated by {1}. One competition is evaluated once -- score this bid on that evaluation.").format(
				frappe.bold(rfq), get_link_to_form(EVALUATION_DOCTYPE, existing)
			),
			title=_("Already being evaluated"),
		)

	bids = rfq_quotations(rfq)

	evaluation = frappe.get_doc(
		{
			"doctype": EVALUATION_DOCTYPE,
			"request_for_quotation": rfq,
			# Who asked for the quotations, which is the one fact the conflict-of-interest rule
			# turns on -- so it is copied from the RFQ rather than defaulted to whoever pressed
			# the button, who may well be somebody else.
			"requested_by": frappe.db.get_value("Request for Quotation", rfq, "owner"),
			"activity_requisition": _requisition_for(quotation),
		}
	).insert()

	return {"name": evaluation.name, "rfq": rfq, "bids": len(bids)}


def _competition(quotation) -> str:
	"""The RFQ this bid was submitted against, refusing the ones that were not competed.

	The RFQ link lives on Supplier Quotation *Item* rather than on the quotation, for the same
	reason `procurement.rfq_quotations` reads it through the child table.
	"""
	rfqs = sorted({row.request_for_quotation for row in quotation.items if row.request_for_quotation})

	if not rfqs:
		frappe.throw(
			_("{0} was not submitted against a Request for Quotation, so there is no competition to evaluate.").format(
				frappe.bold(quotation.name)
			)
			+ "<br><br>"
			+ _("Buying from this supplier without competing it needs an approved Derogation / Waiver Request."),
			title=_("Not a competitive bid"),
		)

	if len(rfqs) > 1:
		frappe.throw(
			_("{0} quotes against more than one Request for Quotation ({1}). Split it, or raise the evaluation from a bid that belongs to one competition.").format(
				frappe.bold(quotation.name), ", ".join(rfqs)
			),
			title=_("More than one competition"),
		)

	return rfqs[0]


def _evaluation_of(rfq: str) -> str | None:
	"""The live evaluation of this competition, if one has been raised."""
	return frappe.db.get_value(
		EVALUATION_DOCTYPE, {"request_for_quotation": rfq, "docstatus": ["<", 2]}, "name"
	)


def _requisition_for(quotation) -> str | None:
	"""The approved requisition this purchase is being made for, when there is exactly one.

	The audit link the evaluation's own `activity_requisition` field is for. Derived through the
	project the quotation carries, and only when a single approved requisition matches it: two
	requisitions on one project is the ordinary case for a long programme, and guessing between
	them would put the wrong budget line on the award. Left empty then, for the buyer to pick.
	"""
	project = quotation.get("project") or next(
		(row.project for row in quotation.items if row.get("project")), None
	)
	if not project:
		return None

	matches = frappe.get_all(
		"Activity Requisition",
		filters={"project": project, "workflow_state": AUTHORISED, "docstatus": 1},
		pluck="name",
		limit=2,
	)
	return matches[0] if len(matches) == 1 else None


# --- a submitted bid -> the waiver that excuses the competition ------------------------------


@frappe.whitelist()
def make_waiver_request(supplier_quotation: str) -> dict:
	"""Open the waiver for a purchase being made without competing it, filled from the bid.

	FoLT's paper waiver asks for nine things. The bid answers three of them outright -- the
	organisation and project heading, the estimated value, and the items or service being bought
	-- and carries the supplier, project and cost centre with them. The six that are left are the
	case being made: the procedure that should have been followed, the one being asked for
	instead, the period, the reasons, the risks created and what will be done about them.

	Two of those six ARE prefilled, and only two: `procedure_should_have`, because what should
	have happened is FoLT's default procurement method rather than anything about this purchase,
	and `requested_procedure`, which is the bare fact of what is being asked for instead. Both
	are ordinary editable text; the justification proper stays blank.

	`ignore_mandatory` is why this saves at all: `reasons` is a required field and the reason is
	precisely what the preparer has to write. A waiver in Draft with no reasons on it is a form
	being filled in, which is what Draft is for -- and it cannot leave Draft without them,
	because the workflow's own transition saves the document and mandatory validation runs then.
	"""
	quotation = _ready_source("Supplier Quotation", supplier_quotation, WAIVER_DOCTYPE)

	existing = frappe.db.get_value(
		WAIVER_DOCTYPE, {"supplier_quotation": quotation.name, "docstatus": ["<", 2]}, "name"
	)
	if existing:
		frappe.throw(
			_("{0} is already the bid on waiver {1}. Make the case there rather than raising a second waiver for the same purchase.").format(
				frappe.bold(quotation.name), get_link_to_form(WAIVER_DOCTYPE, existing)
			),
			title=_("Already waived"),
		)

	project = quotation.get("project") or next(
		(row.project for row in quotation.items if row.get("project")), None
	)

	waiver = frappe.get_doc(
		{
			"doctype": WAIVER_DOCTYPE,
			"organisation_project_name": waiver_heading(
				project, company=quotation.company, activity=quotation.get("title")
			),
			"estimated_value": quotation.grand_total,
			"project": project,
			"cost_center": quotation.get("cost_center"),
			"supplier": quotation.supplier,
			"supplier_quotation": quotation.name,
			"items_service": _quoted_lines(quotation),
			"procedure_should_have": _(
				"Competitive bidding: a Request for Quotation to the pre-qualified supplier "
				"register for the category, evaluated by the Procurement Committee, with the "
				"award approved by the Head of Finance."
			),
			"requested_procedure": _("Single sourcing from {0}, on quotation {1} of {2}.").format(
				quotation.supplier_name or quotation.supplier,
				quotation.name,
				formatdate(quotation.transaction_date),
			),
		}
	)
	waiver.flags.ignore_mandatory = True
	waiver.insert()

	return {
		"name": waiver.name,
		"supplier": quotation.supplier_name or quotation.supplier,
		"value": flt(quotation.grand_total),
		"currency": quotation.currency,
		# What the preparer has to write before this can move, named on arrival rather than
		# discovered one mandatory-field error at a time.
		"to_write": [
			frappe.get_meta(WAIVER_DOCTYPE).get_label(field)
			for field in ("reasons", "risks_created", "mitigating_actions", "period_of_derogation")
		],
	}


@frappe.whitelist()
def get_waiver_heading(project: str | None = None) -> str:
	"""The heading for a waiver being filled in from scratch, from whatever is on the form.

	The blank form asks for "Organisation & Project Name" -- one line, two facts, both of which
	the system already knows: the organisation is FoLT and the project is the one picked on the
	form. Only the waiver raised from a bid was getting it filled in, so the form somebody opens
	from the sidebar left them retyping the name of their own employer.

	Permission-checked on `create`, the way `procurement.get_rfq_quotations` is: this reads a
	Project's name with `frappe.db.get_value`, which ignores permissions by design, and the
	people who raise waivers hold `select` rather than `read` on Project (see
	permissions.LINK_FIELD_PERMISSIONS). Whoever may raise a waiver may see the heading for it;
	nobody else has any reason to ask.
	"""
	frappe.has_permission(WAIVER_DOCTYPE, ptype="create", throw=True)
	return waiver_heading(project)


def waiver_heading(project: str | None, company: str | None = None, activity: str | None = None) -> str:
	"""The waiver's own heading: whose purchase this is, and for what.

	FoLT's form asks for the two together on one line, so they are joined here rather than the
	field being left for somebody to retype what the form already knows. One function for both
	ways in -- the hand-off from a bid and the blank form -- so the two cannot end up writing the
	same heading two different ways.

	`company` is passed in where the source document states it (a bid does), and only guessed at
	from the session where nothing does. `activity` is the fallback for the other half: a
	quotation carries a title even when it names no project, and that is a better answer than the
	organisation on its own.
	"""
	organisation = company or _organisation()
	name = (frappe.db.get_value("Project", project, "project_name") if project else None) or activity

	if organisation and name:
		return f"{organisation} — {name}"

	return organisation or name or ""


def _organisation() -> str | None:
	"""The organisation the heading names, which for FoLT is the company on its books.

	The user's default first. Failing that the company itself, but only where the site has
	exactly one -- on a multi-company site the first row alphabetically would be a guess, and a
	heading naming the wrong entity is worse than a heading the preparer has to finish.
	"""
	default = frappe.defaults.get_user_default("Company")
	if default:
		return default

	companies = frappe.get_all("Company", pluck="name", limit=2)
	return companies[0] if len(companies) == 1 else None


def _quoted_lines(quotation) -> str:
	"""What is being bought, as the quotation priced it -- one line per item.

	The waiver asks for "Items/Service to be Purchased" in prose, and the bid is the most exact
	statement of it there is: the same descriptions, quantities and rates the order will carry.
	"""
	return "\n".join(
		"{qty:g} × {item} @ {rate}".format(
			qty=flt(row.qty),
			item=row.item_name or row.item_code,
			rate=fmt_money(row.rate, currency=quotation.currency),
		)
		for row in quotation.items
	)


# --- either authority -> the order it authorises ---------------------------------------------


@frappe.whitelist()
def make_award_order(committee_evaluation: str) -> dict:
	"""Raise the order for the bid the committee awarded to.

	The order is mapped from the *winning quotation* rather than assembled from the evaluation,
	which is what preserves the audit trail the Implementation Guide asks for: every Purchase
	Order Item carries `supplier_quotation` and `supplier_quotation_item`, so the price on the
	order is traceable to the bid it was competed at.
	"""
	evaluation = _ready_source(EVALUATION_DOCTYPE, committee_evaluation, "Purchase Order")

	if not evaluation.recommended_supplier_quotation:
		frappe.throw(
			_("{0} does not name a winning bid, so there is nothing to order.").format(
				frappe.bold(evaluation.name)
			)
			+ "<br><br>"
			+ _("Set the Winning Supplier Quotation on the evaluation first."),
			title=_("No award on this evaluation"),
		)

	_refuse_second_order("folt_committee_evaluation", evaluation.name, _("award"))

	order = _order_from_quotation(
		evaluation.recommended_supplier_quotation,
		authority_field="folt_committee_evaluation",
		authority=evaluation.name,
		# What the RFQ asked the suppliers to deliver by. The competition's own date, not today's.
		required_by=frappe.db.get_value(
			"Request for Quotation", evaluation.request_for_quotation, "schedule_date"
		),
	)

	return {
		"name": order.name,
		"supplier": order.supplier_name or order.supplier,
		"quotation": evaluation.recommended_supplier_quotation,
		"total": flt(order.grand_total),
		"currency": order.currency,
	}


@frappe.whitelist()
def make_waiver_order(derogation_waiver_request: str) -> dict:
	"""Raise the single-source order an authorised waiver licenses.

	Only from a waiver that has a quotation on it. A waiver raised on its own -- the route the
	Implementation Guide describes, where no quotation precedes the order at all -- authorises an
	order that has to be keyed by hand, because there is nowhere to copy the lines and prices
	from: `items_service` is prose. The gate on the order accepts that order just the same, on
	the strength of this waiver's link.
	"""
	waiver = _ready_source(WAIVER_DOCTYPE, derogation_waiver_request, "Purchase Order")

	if not waiver.supplier_quotation:
		frappe.throw(
			_("{0} has no supplier quotation on it, so the order's lines and prices cannot be derived from anything.").format(
				frappe.bold(waiver.name)
			)
			+ "<br><br>"
			+ _("Raise the Purchase Order for {0} directly and link this waiver on it as the authority.").format(
				frappe.bold(waiver.supplier or _("the supplier"))
			),
			title=_("Nothing to copy from"),
		)

	_refuse_second_order("folt_waiver_request", waiver.name, _("waiver"))

	order = _order_from_quotation(
		waiver.supplier_quotation,
		authority_field="folt_waiver_request",
		authority=waiver.name,
	)

	return {
		"name": order.name,
		"supplier": order.supplier_name or order.supplier,
		"quotation": waiver.supplier_quotation,
		"total": flt(order.grand_total),
		"currency": order.currency,
	}


def _refuse_second_order(authority_field: str, authority: str, what: str):
	"""One decision, one order.

	The form stops offering the button once an order exists (`exclusive` on the hand-off), but the
	server has to refuse it too: the same reasoning activity_chain gives for a payout being
	retired once. A second order off one authority is a second commitment against one decision --
	twice the money, both of them properly approved -- and nothing downstream would notice, since
	each order carries a link to an authority that genuinely authorises it.
	"""
	existing = frappe.db.get_value(
		"Purchase Order", {authority_field: authority, "docstatus": ["<", 2]}, "name"
	)
	if existing:
		frappe.throw(
			_("{0} has already been ordered on {1}. One {2}, one order -- amend that order rather than raising a second one.").format(
				frappe.bold(authority), get_link_to_form("Purchase Order", existing), what
			),
			title=_("Already ordered"),
		)


def _order_from_quotation(quotation: str, authority_field: str, authority: str, required_by=None):
	"""Map a submitted bid onto a draft Purchase Order carrying the authority for it.

	erpnext's own mapper does the work -- items, taxes, currency, the quotation links on every
	row -- rather than the fields being copied here, so an order raised this way is exactly the
	order "Get Items From > Supplier Quotation" would have produced, plus the one thing that
	route could never add: the document that authorised it.

	`schedule_date` is set because erpnext refuses an order without one ("Please enter the
	Required By"), and nothing on a Supplier Quotation answers it -- the bid's `valid_till` is how
	long the price holds, not when the goods are wanted. The RFQ's own Required By is used where
	there is one; failing that the order is dated today and the caller is told to correct it,
	which is the honest version of a date nobody has stated.
	"""
	from erpnext.buying.doctype.supplier_quotation.supplier_quotation import make_purchase_order

	order = make_purchase_order(quotation)
	order.set(authority_field, authority)
	order.transaction_date = nowdate()
	order.schedule_date = required_by or order.schedule_date or nowdate()
	order.insert()

	return order


# --- what happens next ----------------------------------------------------------------------


@frappe.whitelist()
def get_route(doctype: str, name: str) -> dict:
	"""Which procurement route this document is on, and what it can hand on to.

	The Desk gets the hand-offs of a workflow-governed document inside `document_guide.get_guide`
	along with everything else about it. This exists for the one document on the chain that has
	no workflow and therefore no guide: a Supplier Quotation. See public/js/supplier_quotation.js.
	"""
	frappe.has_permission(doctype, doc=name, throw=True)

	doc = frappe.get_doc(doctype, name)

	return {"note": route_note(doc), "handoffs": handoffs_for(doc)}


def handoffs_for(doc) -> list[dict]:
	"""This document's hand-offs, each answered for it: can it be taken, and was it already?

	Returns early for a doctype with none, and that matters: document_guide calls this on every
	form open for every workflow document in the system, so a document that is not on this chain
	has to cost nothing.
	"""
	handoffs = HANDOFFS.get(doc.doctype)
	if not handoffs:
		return []

	state = _state(doc)
	return [_handoff_status(doc, state, handoff) for handoff in handoffs]


def _handoff_status(doc, state: str | None, handoff: Handoff) -> dict:
	"""One arrow out of this document.

	`ready` carries three conditions and the third is the one activity_chain has no need of:
	whether the reader may create the target at all. FoLT hands the two routes out of a bid to
	different people -- the buyer prepares an evaluation, the Operations Support Officer prepares
	a waiver -- so offering both to everybody would mean offering each of them a button that
	throws PermissionError. The route is still described either way (see route_note), which is
	what the person who cannot take it actually needs: who to ask.
	"""
	existing = _existing(doc, handoff)

	ready = (
		state in handoff.ready
		and not (handoff.exclusive and existing)
		and bool(frappe.has_permission(handoff.target, ptype="create"))
	)

	return {
		"label": handoff.label,
		"target": handoff.target,
		"method": f"folt_customizations.procurement_chain.{handoff.method}",
		"arg": handoff.arg,
		"description": handoff.description,
		"ready": ready,
		"ready_at": ", ".join(handoff.ready),
		"existing": existing,
	}


def _existing(doc, handoff: Handoff) -> list[str]:
	"""What this hand-off has already created.

	The evaluation is the exception the `link_field` convention cannot express on its own: it is
	linked to the *competition* rather than to the bid the button was pressed on, so "already
	done" for one bid is already done for every bid on the same RFQ. Asking the bid's own name
	instead would offer a second evaluation of one competition to every other bidder in it.
	"""
	if handoff.target == EVALUATION_DOCTYPE:
		rfqs = {row.request_for_quotation for row in doc.get("items") or [] if row.request_for_quotation}
		return sorted(filter(None, (_evaluation_of(rfq) for rfq in rfqs)))

	return frappe.get_all(
		handoff.target,
		filters={handoff.link_field: doc.name, "docstatus": ["<", 2]},
		pluck="name",
	)


def route_note(doc) -> str | None:
	"""One sentence on where this document sits in FoLT's procurement, for the form to say.

	Only a Supplier Quotation gets one, and it is the sentence the form was missing entirely: a
	submitted bid looked finished, and whether it was one of three bids in a competition or the
	only quotation for a single-source purchase was a fact nobody could see without opening the
	RFQ.
	"""
	if doc.doctype != "Supplier Quotation":
		return None

	if _state(doc) != SUBMITTED:
		return _("Submit this bid to take it on to a committee evaluation or a waiver request.")

	rfqs = sorted({row.request_for_quotation for row in doc.items if row.request_for_quotation})
	if not rfqs:
		return _(
			"Not part of a competition — this bid quotes against no Request for Quotation. "
			"Buying on it needs an approved Derogation / Waiver Request, prepared by the "
			"Operations Support Officer, before an order can be issued."
		)

	bids = rfq_quotations(rfqs[0])
	return _(
		"Competitive bidding — {0} of {1} bid(s) received against {2}. The Procurement Committee "
		"scores them all and recommends an award; the order follows the committee's award."
	).format(
		next((index + 1 for index, bid in enumerate(bids) if bid.supplier_quotation == doc.name), 1),
		len(bids),
		rfqs[0],
	)


# --- shared -----------------------------------------------------------------------------------


def _state(doc) -> str | None:
	"""What step this document is at, whether or not a workflow governs it.

	Both approval routes are workflows and answer with `workflow_state`. The bid they start from
	is not: erpnext submits a Supplier Quotation, from the buyer's form or from the supplier
	portal, and its step is its docstatus. Mapping that onto the same two words the workflows use
	is what lets one `ready` tuple describe every hand-off on the chain.
	"""
	if get_workflow_name(doc.doctype):
		return doc.get(get_workflow(doc.doctype).workflow_state_field)

	return SUBMITTED if doc.docstatus == 1 else UNSUBMITTED


def _ready_source(doctype: str, name: str, target: str):
	"""Load the source document and refuse the hand-off unless it has got that far.

	Checked on the server and not merely hidden in the form, for the reason `activity_chain`
	gives: a hand-off carries an approved document's authority into a new one, so "has it been
	approved" is a rule and not a hint. Here that is the whole point of the module -- an order
	raised off an evaluation still in Committee Reviewing would be an order placed before the
	committee had decided anything.
	"""
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")

	handoff = next((row for row in HANDOFFS.get(doctype, []) if row.target == target), None)
	if not handoff:
		frappe.throw(_("{0} does not lead to a {1}.").format(_(doctype), _(target)))

	state = _state(doc)
	if state not in handoff.ready:
		frappe.throw(
			_("{0} is at {1}. A {2} can only be raised from it once it reaches {3}.").format(
				frappe.bold(doc.name),
				frappe.bold(_(state or _("no state"))),
				_(target),
				frappe.bold(" / ".join(_(row) for row in handoff.ready)),
			),
			title=_("Not ready for the next step"),
		)

	return doc
