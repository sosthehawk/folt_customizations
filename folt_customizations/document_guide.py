"""What the form should be telling you: where this document is, who has it, and what it needs.

Everything a FoLT approval needs in order to explain itself was already in the system and none
of it was on the document. The state field said `Pending Executive Director Approval` and
stopped: not which of six steps that is, not that the Head of Finance signed it on Tuesday, not
who holds the Executive Director role, and not that it is going to be refused at the next step
for want of a signed list nobody has attached yet.

This assembles that into one answer. Almost none of it is new information -- the point is that
it was scattered across four places no user opens:

  workflow_shape       the steps, in order, and whose step each one is
  activity_chain       which of the six SOP documents this is, and what gets raised from it
  workflow             the roles that can move it on, and the people holding them
  Comment rows         what has already happened, when, by whom, and why it was turned down

THE ONE THING THAT IS NEW is `blocked_by`. FoLT gates a state on an attachment -- a list cannot
be marked paid without the acknowledged copy -- and that gate is a `frappe.throw` at the moment
somebody presses the button. The document knows from the moment it is opened that the attachment
is missing and that it will be needed; DOCUMENTS below is what lets it say so first. That is the
whole of the difference between a rule and a rule somebody can plan around.

DOCUMENTS also carries evidence that is expected but gates nothing -- the attendance register's
signed sheet, a requisition's concept note. Those have an empty `required_at`, and the checklist
asks for them without ever claiming they block anything, which is the only honest way to list a
document whose absence stops nothing.

WHAT THIS IS NOT. Nothing here decides anything. Every guard stays where it was: in
workflow_access.enforce_state_custodian, in the transitions' own `allowed` roles, and in the
doctype controllers' validate and before_submit. A badge on a step is a hint about a rule
enforced elsewhere and must never become the rule -- delete this module and the Desk stops
explaining FoLT's chains, but nothing about what they permit changes.
"""

import frappe
from frappe import _
from frappe.model.workflow import get_workflow, get_workflow_name

from folt_customizations import procurement_chain, workflow_shape
from folt_customizations.activity_chain import CHAIN_LENGTH, CHAIN_STEPS, get_chain_status
from folt_customizations.workflow import get_approvers_for_state
from folt_customizations.workflow_access import REJECTION_REASON_FIELD, is_turn_down

# A state change reaches the timeline as a Workflow Comment, and there are three shapes of them
# in FoLT because three different things write one:
#
#   "Approved"                            frappe's own, from apply_workflow -> add_comment
#   "Draft — Two signatures are missing."  workflow_access.record_rejection_reason
#   "Approved → Accounted (derived from…)" float_lifecycle._apply, for the ledger-derived states
#
# The em dash and the arrow are the actual characters stored, not entities -- add_comment decodes
# the `&rarr;` that float_lifecycle writes. Both are matched anyway, so a change at either end
# does not silently stop parsing.
REASON_SEPARATOR = " — "
DERIVED_SEPARATORS = (" → ", " &rarr; ")


class RequiredDoc(frappe._dict):
	"""One piece of evidence a document cannot proceed without."""


# Evidence FoLT's chains gate a step on. Fieldname only, deliberately: the label and the help
# text come from the DocType meta, so what the checklist calls a document is by construction
# what the form calls it, and renaming the field in one place renames it everywhere.
#
# `required_at` is the states the attachment is needed *by*, and it is what makes the chip
# predictive rather than descriptive. `enforced_by` names the method that actually throws, and
# it is not decoration either -- document_guide_e2e drives a real document to each `required_at`
# state with the field cleared and asserts the throw, so an entry here that has quietly stopped
# being enforced fails a test instead of misinforming a user.
#
# ENFORCEMENT IS NOT ADDED HERE. There is deliberately no doc_events validator reading this
# table. The two gates live in their controllers, where the surrounding validation is; a second
# enforcement path over the same rule is how a system starts disagreeing with itself, and the
# doc_events["*"] surface in hooks.py already carries as much as it should.
DOCUMENTS: dict[str, list[RequiredDoc]] = {
	"Activity Participant List": [
		# Expected with every register and gated on nothing. The sheet is evidence of an activity
		# that has already happened and whose attendees are already keyed in, so withholding
		# verification until the scan arrives stalled the chain -- the reimbursement list derives
		# from a *verified* register -- without making the register any more true. The controller
		# says so in a msgprint at submit; an empty `required_at` is how this table says "bring
		# this" rather than "you cannot proceed without this".
		RequiredDoc(fieldname="attendance_sheet", required_at=(), enforced_by=None),
	],
	"Participant Reimbursement List": [
		RequiredDoc(
			fieldname="signed_list",
			required_at=("Paid",),
			enforced_by="validate",
		),
	],
	"Activity Requisition": [
		# Expected with a requisition and gated on nothing, which is the honest status of it
		# today. Listed anyway so the checklist can ask for it; an empty `required_at` is the
		# difference between "bring this" and "you cannot proceed without this", and claiming the
		# second when only the first is true would be the checklist lying about the rules.
		RequiredDoc(fieldname="concept_note", required_at=(), enforced_by=None),
	],
}


@frappe.whitelist()
def get_guide(doctype: str, name: str) -> dict:
	"""Everything the form needs to explain where this document is and what it still needs.

	One call per form open. Assembled rather than derived: the pieces come from the modules that
	already own each question, so there is no second answer here to disagree with them.
	"""
	frappe.has_permission(doctype, doc=name, throw=True)

	shaped = workflow_shape.shape(doctype)
	if not shaped:
		return {}

	doc = frappe.get_doc(doctype, name)
	state = doc.get(shaped["state_field"])
	placed = workflow_shape.locate(shaped, state)

	pending = get_approvers_for_state(get_workflow(doctype), state) if state else {}
	waiting_roles = pending.get("roles") or []

	documents = _documents(doc, doctype)
	blocked_by = [row["label"] for row in documents if row["blocks_next"]]

	# get_chain_status is called rather than inlined. It costs a second get_doc and a second
	# approver resolution -- two queries on a form open -- and it is asserted in six places in
	# activity_chain_e2e, so it is not a function to refactor for two queries.
	chain = get_chain_status(doctype, name) if doctype in CHAIN_STEPS else {}

	# FoLT has a second chain -- procurement, from a submitted bid through the committee or a
	# waiver to the order -- and its hand-offs arrive the same way, so the same buttons render
	# for them with no new plumbing in the Desk. No doctype belongs to both chains, so the two
	# lists cannot disagree about a document; the one document on the procurement chain that is
	# NOT here is the Supplier Quotation, which has no workflow and therefore no guide at all.
	handoffs = (chain.get("handoffs") or []) + procurement_chain.handoffs_for(doc)

	return {
		"doctype": doctype,
		"name": name,
		"state": state,
		"docstatus": doc.docstatus,
		"steps": placed["steps"],
		"lane": placed["lane"],
		"of": placed["of"],
		"at_optional": placed["at_optional"],
		"off_path": placed["off_path"],
		"chain": {
			"step": chain.get("step"),
			"of": CHAIN_LENGTH,
			"step_title": chain.get("step_title"),
		}
		if chain.get("step")
		else None,
		"handoffs": handoffs,
		"waiting_for": {
			"roles": waiting_roles,
			"approvers": pending.get("approvers") or [],
			"unassigned": bool(pending.get("unassigned")),
		},
		# Whether the person reading this is one of the people it is waiting for. Free -- the
		# roles are already in hand -- and it is what turns a tracker from something to look at
		# into something to act on.
		"can_act": bool(set(waiting_roles) & set(frappe.get_roles())),
		"timeline": _timeline(doc, shaped),
		"documents": documents,
		"blocked_by": blocked_by,
		"rejection_reason": doc.get(REJECTION_REASON_FIELD)
		if doc.meta.has_field(REJECTION_REASON_FIELD)
		else None,
	}


# --- what has already happened ----------------------------------------------------------


def _timeline(doc, shaped: dict) -> list[dict]:
	"""The state changes this document has been through, oldest first.

	Read from `Comment` rows rather than from `Workflow Action` or `Version`, and the choice
	matters in both directions. Workflow Action rows are notification plumbing -- created by a
	doc_event, cleaned up when the transition completes, absent entirely on documents that moved
	before that machinery existed -- which workflow.py's own docstring gives as the reason not to
	read them for display. Version rows need `track_changes`, which is not set on Purchase Order,
	Employee Advance or Salary Slip. The Comment row is written unconditionally by
	`apply_workflow` for every transition on every doctype, so it is the one source that is true
	by construction.

	It is also where the rejection reasons are. require_rejection_reason clears the field when a
	document moves on, precisely so that last week's complaint does not travel with it -- so the
	field holds only the current reason and the timeline holds all of them.
	"""
	states = set(shaped["roles_by_state"])
	entries = [
		{
			# The document being raised is the first thing that happened to it, and it is the one
			# entry no transition wrote.
			"state": shaped["first_state"],
			"at": str(doc.creation),
			"by": doc.owner,
			"by_name": _full_name(doc.owner),
			"kind": "raised",
			"reason": None,
		}
	]

	comments = frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": doc.doctype,
			"reference_name": doc.name,
			"comment_type": "Workflow",
		},
		fields=["content", "creation", "owner"],
		order_by="creation asc",
	)

	workflow = get_workflow(doc.doctype)
	previous = shaped["first_state"]

	for comment in comments:
		content = (comment.content or "").strip()
		state, reason, derived = _parse_comment(content)

		# A comment whose head is not a state this workflow has is not one of these entries --
		# somebody's own note, or a state renamed since. Shown as-is rather than dropped: losing
		# a line of a document's history is worse than showing one that does not parse.
		known = state in states

		entries.append(
			{
				"state": state if known else None,
				"at": str(comment.creation),
				"by": comment.owner,
				"by_name": _full_name(comment.owner),
				"kind": _kind(workflow, shaped, previous, state, derived) if known else "note",
				"reason": reason,
				"content": None if known else content,
			}
		)

		if known:
			previous = state

	return _collapse(entries)


def _parse_comment(content: str) -> tuple[str, str | None, bool]:
	"""(state, reason, was_derived) out of one of the three shapes above."""
	for separator in DERIVED_SEPARATORS:
		if separator in content:
			_before, _sep, after = content.partition(separator)
			# "<to> (<why>)" -- the state is what it moved to, and the parenthesis says what in
			# the ledger moved it. Worth keeping: a state that changed with no click behind it is
			# the one people ask about.
			state, _open, why = after.partition(" (")
			return state.strip(), why.rstrip(")").strip() or None, True

	head, _sep, tail = content.partition(REASON_SEPARATOR)
	return head.strip(), tail.strip() or None, False


def _collapse(entries: list[dict]) -> list[dict]:
	"""Merge the two comments a turn-down writes into the one thing that happened.

	A rejection files two rows a couple of milliseconds apart: FoLT's, carrying the reason, from
	`on_update`; then frappe's bare `next_state`, from `apply_workflow` after the save. In the
	Desk's own timeline they render adjacently and read as one entry, which is what
	record_rejection_reason's docstring means by "the second half of the same entry". Here they
	would be two rows saying the same thing, one of them with the reason missing -- so they are
	merged, keeping the reason and the earlier timestamp.
	"""
	collapsed: list[dict] = []
	for entry in entries:
		previous = collapsed[-1] if collapsed else None
		same_state = (
			previous is not None
			and previous["state"] is not None
			and previous["state"] == entry["state"]
			and previous["by"] == entry["by"]
		)
		if same_state and bool(previous["reason"]) != bool(entry["reason"]):
			if entry["reason"]:
				previous["reason"] = entry["reason"]
				previous["kind"] = entry["kind"]
			continue

		collapsed.append(entry)

	return collapsed


def _kind(workflow, shaped: dict, previous: str | None, state: str, derived: bool) -> str:
	"""What sort of move this entry records: progress, a turn-down, a derived state, or the end.

	Asked of `is_turn_down` with the pair of states rather than of the state alone, because the
	state alone cannot answer it. `Return for Correction` on an attendance register goes back to
	`Draft`, and `Draft` is a perfectly good step -- step one. What makes the move a turn-down is
	that it went there from further on, which is a fact about the pair. Reusing the predicate
	also means the timeline agrees with the reason dialog by construction: the entries that
	demanded a reason are exactly the ones that show as turned down.
	"""
	if derived:
		return "derived"
	if previous and is_turn_down(workflow, previous, state):
		return "turned_down"
	if shaped["off_path"].get(state, {}).get("kind") == "turned_down":
		return "turned_down"
	if state in shaped["terminal"]:
		return "finished"
	return "forward"


def _full_name(user: str) -> str:
	return frappe.get_cached_value("User", user, "full_name") or user


# --- what it still needs ----------------------------------------------------------------


def _documents(doc, doctype: str) -> list[dict]:
	"""The declared evidence for this doctype, each with whether it is there and what it gates."""
	required = DOCUMENTS.get(doctype) or []
	if not required:
		return []

	meta = frappe.get_meta(doctype)
	next_states = _next_states(doc, doctype)

	rows = []
	for entry in required:
		field = meta.get_field(entry.fieldname)
		if not field:
			# The registry naming a field the doctype no longer has is a bug worth failing an
			# audit over, not one worth breaking a form over.
			continue

		value = doc.get(entry.fieldname)
		gates = [state for state in entry.required_at if state in next_states]

		rows.append(
			{
				"fieldname": entry.fieldname,
				"label": _(field.label or entry.fieldname),
				"description": _(field.description) if field.description else None,
				"attached": bool(value),
				"url": value or None,
				"required_at": list(entry.required_at),
				"advisory": not entry.required_at,
				# The whole point: this is missing, and the very next step needs it.
				"blocks_next": not value and bool(gates),
				"blocks": gates,
			}
		)

	return rows


def _next_states(doc, doctype: str) -> set[str]:
	"""The states this document could move into next, turn-downs excluded.

	Taken from the workflow graph rather than from `get_transitions`, deliberately: a checklist
	says what the document needs, which does not change according to who is looking at it. Whether
	*this* user may make the move is a separate question, and `can_act` answers it.
	"""
	if not get_workflow_name(doctype):
		return set()

	workflow = get_workflow(doctype)
	state = doc.get(workflow.workflow_state_field)
	if not state:
		return set()

	return {
		row.next_state
		for row in workflow.transitions
		if row.state == state
		and row.next_state != state
		and not is_turn_down(workflow, row.state, row.next_state)
	}


# --- the Desk's copy of the static half -------------------------------------------------


def guide_map(only_permitted: bool = False) -> dict:
	"""The shape of every active workflow, keyed by doctype: the static half of the guide.

	Static meaning a property of the *workflow* rather than of any document -- the steps, whose
	each one is, which are also steps in the six-document SOP -- so it is the same answer for
	every Activity Requisition in the system. That is what makes it cheap enough to hand a client
	once per session and then place documents against without another round trip.

	Split out of add_guide_to_bootinfo so a second caller can have it. The Desk gets it through
	`extend_bootinfo`, which does not run outside /app, so anything served from a website route
	needs this function directly rather than a second implementation of it.

	`only_permitted` filters to the doctypes the session user can read. The boot payload does not
	do that -- it hands every user all nine workflows and the role names on each step -- which is
	tolerable for the Desk, where the same user could read the Workflow list anyway, and is worth
	tightening for anything new. It is off by default so the bootinfo hook keeps its exact
	existing output; document_guide_e2e asserts that shape.
	"""
	guided = {}
	for name in frappe.get_all("Workflow", filters={"is_active": 1}, pluck="name"):
		workflow = frappe.get_cached_doc("Workflow", name)
		shaped = workflow_shape.shape(workflow.document_type)
		if not shaped:
			continue

		if only_permitted and not frappe.has_permission(workflow.document_type, "read"):
			continue

		step, title = CHAIN_STEPS.get(workflow.document_type, (None, None))
		guided[workflow.document_type] = {
			"state_field": shaped["state_field"],
			"lanes": [
				{
					"rank": lane["rank"],
					"label": _(lane["label"]),
					"states": lane["states"],
					"optional": lane["optional"],
					"roles": lane["roles"],
					"terminal": lane["terminal"],
				}
				for lane in shaped["lanes"]
			],
			"off_path": shaped["off_path"],
			"chain": {"step": step, "of": CHAIN_LENGTH, "title": title} if step else None,
			"documents": [entry.fieldname for entry in DOCUMENTS.get(workflow.document_type) or []],
		}

	return guided


def add_guide_to_bootinfo(bootinfo):
	"""Hand the Desk the shape of every active workflow. `extend_bootinfo` hook.

	A form script can only be attached by doctype and the list of doctypes has to be known before
	any form is opened, which is the same reason add_turn_downs_to_bootinfo exists.

	This replaced activity_chain.add_chain_to_bootinfo rather than sitting beside it. That key said
	which doctypes are steps in the six-document SOP, which is a subset of what is here, and two
	boot keys describing one fact is exactly the drift this app writes its comments to avoid.
	"""
	bootinfo.folt_guide = guide_map()


# --- audit ------------------------------------------------------------------------------


def audit(verbose: bool = True) -> dict:
	"""Check the DOCUMENTS registry against the doctypes it describes.

	Two ways for it to be wrong, and only one of them is visible in the Desk: a field that no
	longer exists shows as a missing row, while a state that no longer exists shows as nothing at
	all -- the chip silently stops being predictive and the throw comes back as a surprise. Run
	after any change to the registry or to a workflow's states:

	    bench --site folt.localhost execute folt_customizations.document_guide.audit
	"""
	problems = []

	for doctype, entries in DOCUMENTS.items():
		if not frappe.db.exists("DocType", doctype):
			problems.append(f"{doctype}: doctype does not exist")
			continue

		meta = frappe.get_meta(doctype)
		shaped = workflow_shape.shape(doctype)
		known = set(shaped["roles_by_state"]) if shaped else set()

		for entry in entries:
			field = meta.get_field(entry.fieldname)
			if not field:
				problems.append(f"{doctype}.{entry.fieldname}: no such field")
			elif field.fieldtype not in ("Attach", "Attach Image"):
				problems.append(
					f"{doctype}.{entry.fieldname}: is a {field.fieldtype}, not an attachment"
				)

			for state in entry.required_at:
				if state not in known:
					problems.append(
						f"{doctype}.{entry.fieldname}: required_at names {state!r}, "
						f"which is not a state of {shaped['workflow'] if shaped else 'any workflow'}"
					)

			if entry.required_at and not entry.enforced_by:
				problems.append(
					f"{doctype}.{entry.fieldname}: says it is required at "
					f"{', '.join(entry.required_at)} but names nothing that enforces it"
				)

	if verbose:
		for doctype, entries in DOCUMENTS.items():
			print(f"\n{doctype}")
			for entry in entries:
				gate = ", ".join(entry.required_at) or "nothing (advisory)"
				print(f"  {entry.fieldname}  required by: {gate}  enforced by: {entry.enforced_by}")

		print("\nPROBLEMS" if problems else "\nno problems")
		for problem in problems:
			print(f"  {problem}")

	return {"problems": problems}
