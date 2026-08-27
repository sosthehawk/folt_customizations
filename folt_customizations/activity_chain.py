"""The activity float chain: each step hands the next one its contents.

FoLT's Finance SOP is six documents long, and until now every one of them was opened blank.
The requisition named the activity, the budget line, the donor and the float holder; the float
request asked for all four again. The register named the activity and its date; the
reimbursement list asked again. The reimbursement list held the payees and what each was paid;
the retirement claim asked for the total by hand. Nothing was wrong with the approval chains --
what was missing was the hand-off between them, so the same facts were re-keyed five times and
the person finishing one step had to know, unaided, which document came next.

This module is that hand-off, and it is deliberately one module rather than a button per
doctype: the chain is a single fact about how FoLT works, and it is stated once here.

    step 1  Activity Requisition          raised by programme staff
                                          -> Head of Programs -> Head of Finance
    step 2  Employee Advance              the Float Request Form
                                          -> Finance Officer -> Executive Director
    step 3  Payment Entry                 Finance Assistant pays the float (ERPNext's own)
    step 4  Activity Participant List     the attendance register, on the activity date
                                          -> Head of Programs verifies
    step 5  Participant Reimbursement List who is paid what, derived from the register
                                          -> Finance Officer -> Executive Director -> paid
    step 6  Expense Claim                 the Float Expense Report, retiring the float
                                          -> Finance Officer -> Executive Director -> settled

Two rules hold everywhere in here:

*A hand-off copies facts, it does not invent them.* Every value a maker writes is one the
source document already carries, or a company default. Where a fact is genuinely new at the
next step -- who actually attended, what the fuel receipt says -- the field is left empty.

*A hand-off is recorded on the document it creates.* Each target carries a link back to its
source (`folt_activity_requisition`, `activity_requisition`, `attendance_reference`,
`folt_reimbursement_list`), which is what makes the chain navigable afterwards and what
`get_chain_status` reads to say what has already been created.
"""

import frappe
from frappe import _
from frappe.model.workflow import get_workflow, get_workflow_name
from frappe.utils import flt, nowdate

from folt_customizations.folt_customizations.doctype.participant_reimbursement_list.participant_reimbursement_list import (
	fetch_participants,
)
from folt_customizations.workflow import get_approvers_for_state

# The states a float has to have reached before money can be committed against it. Approved is
# not enough: a reimbursement list is a payment instruction, and it cannot be prepared against a
# float that has not been disbursed. See float_lifecycle for how these are derived.
FUNDED_FLOAT_STATES = ("Disbursed", "Overdue", "Accounted")

# A reimbursement list is retired once the payout has happened and the participants have
# acknowledged it -- Partly Paid included, because a list with a failed payee still has to
# account for the money that did go out.
RETIRABLE_LIST_STATES = ("Partly Paid", "Paid")


class Handoff(frappe._dict):
	"""One arrow in the chain: what this document can create next, and when."""


HANDOFFS: dict[str, list[Handoff]] = {
	"Activity Requisition": [
		Handoff(
			label="Float Request",
			target="Employee Advance",
			method="make_float_request",
			arg="activity_requisition",
			ready=("Approved",),
			link_field="folt_activity_requisition",
			# An activity with no cash component -- a procured service, a venue paid direct --
			# has no float to request, and offering the button anyway would invite one.
			only_if="float_required",
			description="The Float Request Form, filled from this requisition.",
		),
		Handoff(
			label="Attendance Register",
			target="Activity Participant List",
			method="make_attendance_register",
			arg="activity_requisition",
			ready=("Approved",),
			link_field="activity_requisition",
			description="Opened for the activity date, ready for attendees on the day.",
		),
	],
	"Activity Participant List": [
		Handoff(
			label="Reimbursement List",
			target="Participant Reimbursement List",
			method="make_reimbursement_list",
			arg="register",
			ready=("Verified",),
			link_field="attendance_reference",
			# One register, one list. See make_reimbursement_list for why a second one is refused
			# rather than merely discouraged.
			exclusive=True,
			description="Eligible attendees and their scheduled rates, pulled from this register.",
		),
	],
	"Participant Reimbursement List": [
		Handoff(
			label="Float Retirement",
			target="Expense Claim",
			method="make_float_retirement",
			arg="reimbursement_list",
			ready=RETIRABLE_LIST_STATES,
			link_field="folt_reimbursement_list",
			# A payout is accounted for once -- see make_float_retirement.
			exclusive=True,
			description="The Float Expense Report: what was paid out, against the float.",
		),
	],
}

# Where each document sits in the SOP, for the banner. Payment Entry is step 3 and has no entry
# here on purpose: it is ERPNext's own document with ERPNext's own form, and the float it pays
# already says on its face what is happening to it.
CHAIN_STEPS = {
	"Activity Requisition": (1, "Activity planning and budget approval"),
	"Employee Advance": (2, "Float request, check and approval"),
	"Activity Participant List": (4, "Attendance register"),
	"Participant Reimbursement List": (5, "Reimbursement list, approval and payout"),
	"Expense Claim": (6, "Float retirement and filing"),
}
CHAIN_LENGTH = 6


# --- step 1 -> step 2: the Float Request Form ---------------------------------------------


@frappe.whitelist()
def make_float_request(activity_requisition: str) -> str:
	"""Open the float request for an approved requisition, filled from it.

	Everything the Float Request Form asks for -- amount, budget line, donor, activity, who
	holds the float -- was decided when the requisition was approved. What is left for the
	Finance Officer is to check it, which is step 2's own transition.
	"""
	requisition = _ready_source("Activity Requisition", activity_requisition, "Employee Advance")

	if not requisition.float_required:
		frappe.throw(
			_("{0} is not marked as needing a cash float. Tick <b>Cash float required</b> on the requisition first.").format(
				frappe.bold(requisition.name)
			),
			title=_("No float on this requisition"),
		)

	defaults = _company_defaults(requisition.company)
	if not defaults.advance_account:
		frappe.throw(
			_("{0} has no Default Employee Advance Account set, so a float cannot be raised against it.").format(
				frappe.bold(requisition.company)
			),
			title=_("Company not set up for floats"),
		)

	advance = frappe.get_doc(
		{
			"doctype": "Employee Advance",
			"employee": requisition.float_holder or requisition.requested_by,
			"company": requisition.company,
			"posting_date": nowdate(),
			"currency": defaults.currency,
			"exchange_rate": 1,
			"purpose": _("Float for {0}").format(requisition.activity_program),
			"advance_amount": requisition.float_amount or requisition.budget_amount,
			"advance_account": defaults.advance_account,
			"folt_project": requisition.project,
			"folt_budget_line": requisition.budget_line,
			"folt_donor_code": requisition.donor,
			"folt_activity_requisition": requisition.name,
		}
	).insert()

	return advance.name


# --- step 1 -> step 4: the attendance register ---------------------------------------------


@frappe.whitelist()
def make_attendance_register(activity_requisition: str) -> str:
	"""Open the register for the activity, on the date the requisition was approved for.

	The attendees themselves are the one thing in this chain that cannot be copied forward: who
	turned up is discovered on the day. So the register comes out headed and empty, which is
	exactly what the programme officer carries into the room.
	"""
	requisition = _ready_source("Activity Requisition", activity_requisition, "Activity Participant List")

	if not requisition.project:
		frappe.throw(
			_("{0} has no Project. Approving a requisition opens one; set it on the requisition to derive a register.").format(
				frappe.bold(requisition.name)
			),
			title=_("No activity to register against"),
		)

	register = frappe.get_doc(
		{
			"doctype": "Activity Participant List",
			"activity": requisition.project,
			"activity_title": requisition.activity_program,
			"session_date": requisition.activity_date or nowdate(),
			"venue": requisition.venue,
			"activity_requisition": requisition.name,
		}
	).insert()

	return register.name


# --- step 4 -> step 5: the reimbursement list ----------------------------------------------


@frappe.whitelist()
def make_reimbursement_list(register: str, employee_advance: str | None = None) -> dict:
	"""Derive the reimbursement list from a verified register, payees and rates included.

	This was four actions -- open a blank list, find the float, save, press *Fetch participants
	from register* -- and the first three of them were re-keying what the register and the float
	already said. It is one now, and the fetch is the same code the button calls, so a list made
	this way is indistinguishable from one made by hand.

	Returns the created list plus what the fetch found, so the caller can say so; or, when the
	project has more than one funded float, `needs_float` and the candidates, because which float
	pays for a list is a finance decision and not something to guess at.
	"""
	source = _ready_source("Activity Participant List", register, "Participant Reimbursement List")

	# One register, one reimbursement list. Making the derivation a single click also makes it
	# easy to click twice, and a second list off the same register pays the same people again --
	# undetectably, as long as the float is big enough to cover both. Anyone the first list
	# deliberately left off is added to that list, not to a new one.
	existing = frappe.db.get_value(
		"Participant Reimbursement List",
		{"attendance_reference": source.name, "docstatus": ["<", 2]},
		"name",
	)
	if existing:
		frappe.throw(
			_(
				"{0} is already the register for {1}. Add anybody missing to that list rather than "
				"deriving a second one from the same register."
			).format(
				frappe.bold(source.name),
				frappe.utils.get_link_to_form("Participant Reimbursement List", existing),
			),
			title=_("Already derived"),
		)

	if not employee_advance:
		candidates = funded_floats(source.activity)
		if not candidates:
			frappe.throw(
				_(
					"No disbursed float on {0}. A reimbursement list pays out of a float, so the "
					"float has to be approved and paid first (step 2 and step 3)."
				).format(frappe.bold(source.activity)),
				title=_("Nothing to pay out of"),
			)
		if len(candidates) > 1:
			return {"needs_float": True, "activity": source.activity, "floats": candidates}
		employee_advance = candidates[0]["name"]

	reimbursement_list = frappe.get_doc(
		{
			"doctype": "Participant Reimbursement List",
			"employee_advance": employee_advance,
			"activity": source.activity,
			"attendance_reference": source.name,
		}
	).insert()

	fetched = fetch_participants(reimbursement_list.name, register=source.name)

	return {"name": reimbursement_list.name, **fetched}


@frappe.whitelist()
def funded_floats(activity: str) -> list[dict]:
	"""Floats on this activity that have money in them, newest first."""
	return frappe.get_all(
		"Employee Advance",
		filters={
			"folt_project": activity,
			"docstatus": 1,
			"workflow_state": ["in", FUNDED_FLOAT_STATES],
		},
		fields=["name", "employee_name", "advance_amount", "paid_amount", "workflow_state"],
		order_by="posting_date desc",
	)


# --- step 5 -> step 6: the Float Expense Report ---------------------------------------------


@frappe.whitelist()
def make_float_retirement(reimbursement_list: str) -> dict:
	"""Open the retirement claim for the float this list was paid out of.

	One expense row per paid reimbursement list on the float, not just the one the button was
	pressed on: the Float Expense Report accounts for the whole float in one document, which is
	what makes its balance mean anything. Receipts that are not participant payments -- the fuel
	docket, the M-Pesa charges -- are added by the Finance Officer as further rows, because
	nothing upstream in this chain knows about them.
	"""
	source = _ready_source("Participant Reimbursement List", reimbursement_list, "Expense Claim")

	if not source.employee_advance:
		frappe.throw(_("{0} is not linked to a float, so there is nothing to retire.").format(source.name))

	already = _retired_by(source.name)
	if already:
		frappe.throw(
			_("{0} has already been retired by {1}. A payout is accounted for once.").format(
				frappe.bold(source.name), frappe.utils.get_link_to_form("Expense Claim", already)
			),
			title=_("Already retired"),
		)

	advance = frappe.get_doc("Employee Advance", source.employee_advance)
	lists = _lists_to_retire(advance.name)

	defaults = _company_defaults(advance.company)
	claim_type = _expense_claim_type(advance.company)
	posting_date = nowdate()

	expenses = [
		{
			"expense_date": row.session_date or posting_date,
			"expense_type": claim_type,
			"description": _("Participant reimbursement — {0} ({1} payees, {2})").format(
				row.activity_title or row.activity, row.payees, row.name
			),
			"amount": row.total_paid,
			"sanctioned_amount": row.total_paid,
			"cost_center": defaults.cost_center,
		}
		for row in lists
	]

	spent = sum(flt(row.total_paid) for row in lists)
	unclaimed = flt(advance.paid_amount) - flt(advance.claimed_amount)

	claim = frappe.get_doc(
		{
			"doctype": "Expense Claim",
			"employee": advance.employee,
			"company": advance.company,
			"posting_date": posting_date,
			"payable_account": defaults.payable_account,
			"currency": defaults.currency,
			"exchange_rate": 1,
			"project": source.activity,
			"cost_center": defaults.cost_center,
			"remark": _("Float retirement for {0} (float {1}, budget line {2})").format(
				source.activity, advance.name, advance.folt_budget_line or _("not stated")
			),
			"folt_reimbursement_list": source.name,
			"expenses": expenses,
			"advances": [
				{
					"employee_advance": advance.name,
					"posting_date": advance.posting_date,
					"advance_account": advance.advance_account,
					"advance_paid": advance.paid_amount,
					"unclaimed_amount": unclaimed,
					# Left unset this reads as a total exchange loss on the allocation and hrms
					# posts a gain/loss Journal Entry the approver has no business creating --
					# it is what "Get Advances" fills in on the form by hand.
					"exchange_rate": advance.get("exchange_rate") or 1,
					"allocated_amount": min(spent, unclaimed) if unclaimed > 0 else spent,
				}
			],
		}
	).insert()

	return {
		"name": claim.name,
		"lists": [row.name for row in lists],
		"spent": spent,
		"float_paid": flt(advance.paid_amount),
		"balance": flt(advance.paid_amount) - spent,
	}


def _retired_by(reimbursement_list: str) -> str | None:
	"""The live claim already accounting for this list, if there is one.

	A payout is retired once. Without this the button would simply make a second claim over the
	same money -- and the float's balance, which is the whole point of the Float Expense Report,
	would read as over-spent.
	"""
	return frappe.db.get_value(
		"Expense Claim", {"folt_reimbursement_list": reimbursement_list, "docstatus": ["<", 2]}, "name"
	)


def _lists_to_retire(advance: str) -> list[frappe._dict]:
	"""Paid reimbursement lists on this float that no live claim already accounts for.

	More than one, in the ordinary case: a float pays for several sessions of the same activity
	and the Float Expense Report accounts for the whole of it in one document.
	"""
	rows = frappe.get_all(
		"Participant Reimbursement List",
		filters={
			"employee_advance": advance,
			"docstatus": 1,
			"workflow_state": ["in", RETIRABLE_LIST_STATES],
		},
		fields=["name", "activity", "attendance_reference", "total_paid"],
		order_by="creation asc",
	)

	lists = [frappe._dict(row) for row in rows if not _retired_by(row.name)]

	for row in lists:
		row.payees = frappe.db.count(
			"Participant Reimbursement Entry", {"parent": row.name, "payment_status": "Paid"}
		)
		row.session_date, row.activity_title = (
			frappe.db.get_value(
				"Activity Participant List", row.attendance_reference, ["session_date", "activity_title"]
			)
			if row.attendance_reference
			else (None, None)
		)

	return lists


# --- what happens next --------------------------------------------------------------------


@frappe.whitelist()
def get_chain_status(doctype: str, name: str) -> dict:
	"""Where this document is in the SOP, who it is waiting for, and what comes after it.

	Both halves are derived rather than written down twice: the pending actor comes from the
	workflow's own transitions (workflow.get_approvers_for_state) and the hand-offs from
	HANDOFFS above. A chain that gains a state next month gains the right banner with it.
	"""
	frappe.has_permission(doctype, doc=name, throw=True)

	doc = frappe.get_doc(doctype, name)
	state = doc.get("workflow_state")
	step, title = CHAIN_STEPS.get(doctype, (None, None))

	pending = (
		get_approvers_for_state(get_workflow(doctype), state)
		if state and get_workflow_name(doctype)
		else {}
	)

	return {
		"step": step,
		"of": CHAIN_LENGTH,
		"step_title": title,
		"state": state,
		"waiting_for": pending.get("roles") or [],
		"unassigned": bool(pending.get("unassigned")),
		"handoffs": [_handoff_status(doc, state, handoff) for handoff in HANDOFFS.get(doctype, [])],
	}


def _handoff_status(doc, state: str, handoff: Handoff) -> dict:
	"""One arrow, answered for this document: can it be taken, and was it already?"""
	existing = frappe.get_all(
		handoff.target,
		filters={handoff.link_field: doc.name, "docstatus": ["<", 2]},
		pluck="name",
	)

	ready = state in handoff.ready
	if ready and handoff.only_if and not doc.get(handoff.only_if):
		ready = False
	# An arrow that may only be taken once stops being offered once it has been: the server
	# refuses it either way, and a button that always throws is worse than no button.
	if ready and handoff.exclusive and existing:
		ready = False

	return {
		"label": handoff.label,
		"target": handoff.target,
		"method": f"folt_customizations.activity_chain.{handoff.method}",
		"arg": handoff.arg,
		"description": handoff.description,
		"ready": ready,
		"ready_at": ", ".join(handoff.ready),
		"existing": existing,
	}


# --- shared ---------------------------------------------------------------------------------


def _ready_source(doctype: str, name: str, target: str):
	"""Load the source document and refuse the hand-off unless it has got that far.

	Checked on the server and not merely hidden in the form: a hand-off carries an approved
	document's authority into a new one, so "has it been approved" is a rule and not a hint.
	"""
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")

	handoff = next((row for row in HANDOFFS.get(doctype, []) if row.target == target), None)
	if not handoff:
		frappe.throw(_("{0} does not lead to a {1}.").format(_(doctype), _(target)))

	if doc.get("workflow_state") not in handoff.ready:
		frappe.throw(
			_("{0} is at {1}. A {2} can only be raised from it once it reaches {3}.").format(
				frappe.bold(doc.name),
				frappe.bold(_(doc.get("workflow_state") or _("no state"))),
				_(target),
				frappe.bold(" / ".join(_(s) for s in handoff.ready)),
			),
			title=_("Not ready for the next step"),
		)

	return doc


def _company_defaults(company: str) -> frappe._dict:
	return frappe._dict(
		{
			"currency": frappe.db.get_value("Company", company, "default_currency"),
			"cost_center": frappe.db.get_value("Company", company, "cost_center"),
			"advance_account": frappe.db.get_value("Company", company, "default_employee_advance_account"),
			"payable_account": frappe.db.get_value("Company", company, "default_payable_account"),
		}
	)


def _expense_claim_type(company: str) -> str:
	"""A claim type with an account configured on this company.

	hrms refuses to validate a claim whose type has no account for the company, and most of the
	types ERPNext ships have none -- so the type is chosen by what is actually set up. FoLT's own
	name for participant payments wins when it is configured; otherwise the first that is, and
	the Finance Officer can change it on the row.
	"""
	configured = frappe.get_all(
		"Expense Claim Account",
		filters={"company": company, "default_account": ["is", "set"]},
		pluck="parent",
		order_by="parent asc",
	)

	if not configured:
		frappe.throw(
			_(
				"No Expense Claim Type has a default account on {0}. Set one up before retiring a "
				"float, or the claim cannot post."
			).format(frappe.bold(company)),
			title=_("Expense Claim Type not set up"),
		)

	for preferred in ("Participant Reimbursement", "Travel"):
		if preferred in configured:
			return preferred

	return configured[0]


# Which doctypes are steps in this chain used to be sent to the Desk from here, as
# `bootinfo.folt_chain`. document_guide.add_guide_to_bootinfo now carries it, alongside the step
# plan of every workflow -- CHAIN_STEPS and CHAIN_LENGTH above are what it reads. One boot key
# describing where a document sits, rather than two describing overlapping halves of it.
