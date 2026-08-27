"""The shape of an approval chain: its steps, in order, and whose step each one is.

Every FoLT workflow already contains its own step plan. `fixtures/workflow.json` gives each
state a place in the sequence (implied by the transitions between them), an `allow_edit` role
that owns it, and a `doc_status` that says whether reaching it submits the document. Nine
workflows, fifty-odd states. None of it was ever rendered as a sequence, so the document told
you where it *is* ("Pending Head of Finance") and never where that sits in the whole -- which
is the question anybody looking at a half-finished approval actually has.

WHY THIS IS DERIVED AND NOT WRITTEN DOWN. A hand-written list of steps per doctype would be a
second copy of the workflow, and `bench migrate` re-imports the first copy from disk on every
run. The two would part company the first time somebody inserted a review step, and the symptom
-- a tracker quietly one step out of date -- is not the kind of thing anybody reports. So the
sequence is computed from the workflow itself, and a chain that gains a state next month gains
the right tracker with it, with nothing to remember.

The one thing that cannot be read off the graph is which edges are progress and which are a
document being sent back. `workflow_access.is_turn_down` already answers that, by the shape of
the workflow rather than the wording of the action, and it is reused here rather than
re-derived: the reject branches simply are not edges as far as this module is concerned, so
`Rejected` never appears as a step in a tracker and never has to be special-cased out of one.

Nothing here reads a document. `shape()` is a property of the *workflow*, so it is the same
answer for every Activity Requisition in the system -- which is what makes it cheap enough to
put in the boot payload and hand to the Desk once per session (see document_guide).
"""

import frappe
from frappe.model.workflow import get_workflow, get_workflow_name

from folt_customizations.workflow_access import is_turn_down

# The escape hatch, and deliberately empty: all nine FoLT workflows derive correctly, and an
# override here is a statement that the derivation has met a shape it reads wrongly. Keyed by
# Workflow name, the value being the full ordered list of states that make up the required
# spine -- states left out are classified as if the derivation had left them out.
#
# Reach for this only after shape_audit() has shown you what the derivation decided and you are
# satisfied it is wrong. A wrong tracker is a cosmetic bug; a plan written out here that nobody
# updates when the workflow changes is the drift this module exists to avoid.
STEP_PLAN: dict[str, list[str]] = {}


def shape(doctype: str) -> dict | None:
	"""The ordered steps of `doctype`'s workflow, and who owns each. None if it has no workflow.

	Cached per workflow for the length of the request. `get_cached_doc` is doing the real work --
	the same load `workflow_access.add_turn_downs_to_bootinfo` already makes on every boot -- so
	this adds no cache of its own and therefore nothing new to invalidate.
	"""
	if not get_workflow_name(doctype):
		return None

	workflow = get_workflow(doctype)
	return _shape_of(workflow)


def _shape_of(workflow) -> dict:
	"""The same answer for a Workflow already in hand, so callers looping over all nine
	do not each pay a second lookup to get back the document they started from."""
	states = [row.state for row in workflow.states]
	first = states[0] if states else None

	ranked, optional = _plan(workflow, states, first)
	lanes = _lanes(workflow, ranked, optional)
	on_path = {state for lane in lanes for state in lane["states"] + lane["optional"]}

	return {
		"workflow": workflow.name,
		"doctype": workflow.document_type,
		"state_field": workflow.workflow_state_field,
		"first_state": first,
		"lanes": lanes,
		"off_path": _off_path(workflow, states, on_path, first),
		"terminal": [state for lane in lanes if lane["terminal"] for state in lane["states"]],
		"roles_by_state": {state: _movers(workflow, state) for state in states},
		"custodian_by_state": {
			row.state: row.allow_edit for row in workflow.states if row.allow_edit
		},
	}


# --- ordering ---------------------------------------------------------------------------


def _forward_edges(workflow) -> dict[str, list[str]]:
	"""The workflow with its turn-downs removed: progress only.

	A self-loop is dropped too. `is_turn_down` reports False for one (from_state == to_state is
	not a document being sent back), but it is not progress either, and left in it would rank a
	state as its own successor.
	"""
	edges: dict[str, list[str]] = {}
	for row in workflow.transitions:
		if row.state == row.next_state:
			continue
		if is_turn_down(workflow, row.state, row.next_state):
			continue
		edges.setdefault(row.state, [])
		if row.next_state not in edges[row.state]:
			edges[row.state].append(row.next_state)
	return edges


def _reaches(edges: dict[str, list[str]], start: str) -> set[str]:
	"""Every state reachable from `start` by progress edges. Cycle-safe."""
	seen: set[str] = set()
	stack = list(edges.get(start, []))
	while stack:
		state = stack.pop()
		if state in seen:
			continue
		seen.add(state)
		stack.extend(edges.get(state, []))
	return seen


def _optional_states(edges: dict[str, list[str]]) -> set[str]:
	"""States a document does not have to pass through, because its predecessor can skip it.

	THE SHAPE. FoLT has three of these and they look identical in the graph, though nothing in
	their names says so:

	  Disbursed -> Accounted            and  Disbursed -> Overdue -> Accounted
	  Approved  -> Paid                 and  Approved  -> Partly Paid -> Paid
	  Draft     -> Pending FO Review    and  Draft     -> Pending Committee Review -> Pending FO Review

	In each case a state sits on a loop off the trunk that rejoins it further on, and the trunk
	itself is still there. A float may go overdue or may not; a list may be partly paid first or
	may not; a waiver may go to the Procurement Committee first or straight to the Finance
	Officer. So none of the three is a *step* -- calling `Overdue` step five would say every
	float goes overdue -- and none of them is a detour either, because they carry the document
	forward. They are optional, and that is what they get called.

	This is the rule that means FoLT needs no hand-written step plans at all. It is worth having
	rather than three STEP_PLAN entries because the next workflow with an optional review step
	gets read correctly without anybody noticing it needed to be.

	Formally: S is optional when some predecessor P of S has another progress edge P -> T, with
	T not S, and S can still reach T. `Accounted` is not optional by this rule even though
	`Disbursed -> Overdue` exists, because `Accounted` cannot reach `Overdue`; that asymmetry is
	the whole of what distinguishes the skippable state from the one it skips to.
	"""
	predecessors: dict[str, list[str]] = {}
	for source, targets in edges.items():
		for target in targets:
			predecessors.setdefault(target, []).append(source)

	optional = set()
	for state, parents in predecessors.items():
		downstream = _reaches(edges, state)
		for parent in parents:
			if any(sibling != state and sibling in downstream for sibling in edges.get(parent, [])):
				optional.add(state)
				break

	return optional


def _spine_edges(edges: dict[str, list[str]], optional: set[str]) -> dict[str, list[str]]:
	"""The progress graph with the optional states contracted out.

	An edge into an optional state becomes edges to whatever that state leads to, following
	chains of them, so ranking sees only the states a document must actually pass through.
	"""

	def landings(state: str, seen: frozenset[str] = frozenset()) -> list[str]:
		if state not in optional:
			return [state]
		if state in seen:
			return []
		out = []
		for target in edges.get(state, []):
			for landing in landings(target, seen | {state}):
				if landing not in out:
					out.append(landing)
		return out

	spine: dict[str, list[str]] = {}
	for source, targets in edges.items():
		if source in optional:
			continue
		for target in targets:
			for landing in landings(target):
				if landing == source:
					continue
				spine.setdefault(source, [])
				if landing not in spine[source]:
					spine[source].append(landing)

	return spine


def _plan(workflow, states: list[str], first: str | None) -> tuple[list[tuple[int, str]], set[str]]:
	"""(rank, state) for the required spine, in order, plus the optional states.

	The override wins where there is one, so a workflow the derivation reads wrongly is fixed by
	stating its order rather than by making the derivation cleverer and hoping.
	"""
	edges = _forward_edges(workflow)

	if workflow.name in STEP_PLAN:
		planned = [state for state in STEP_PLAN[workflow.name] if state in states]
		return list(enumerate(planned)), set()

	optional = _optional_states(edges) - {first}
	ranked = _rank(_spine_edges(edges, optional), states, first)
	return _drop_detours(edges, ranked), optional


def _rank(edges: dict[str, list[str]], states: list[str], first: str | None) -> list[tuple[int, str]]:
	"""Rank the states by how far along the chain they are, breadth first from the first state.

	WHY BREADTH FIRST AND NOT LONGEST PATH. Longest path is the more obvious reading of "how far
	along is this" and it does not terminate here: a reimbursement list can go
	`Paid -> Raise Dispute -> Disputed -> Resolve Dispute -> Paid`, and neither leg is a turn-down
	(a dispute is submitted and has a way out, so it is a thread that continues -- see
	is_turn_down), so the graph genuinely contains a cycle. Breadth first assigns each state the
	distance it is first reached at and never revisits it, which terminates on any graph.

	Iteration follows `workflow.states` order within a rank so the result is stable: two states
	at the same distance come out in the order the fixture lists them, not in whatever order the
	transitions happened to be written.
	"""
	if not first:
		return []

	order = {state: i for i, state in enumerate(states)}
	rank = {first: 0}
	frontier = [first]

	while frontier:
		frontier.sort(key=lambda state: order.get(state, len(order)))
		nxt = []
		for state in frontier:
			for target in edges.get(state, []):
				# Already ranked means already reached at this distance or a shorter one, and a
				# shorter one is the answer we keep.
				if target in rank:
					continue
				rank[target] = rank[state] + 1
				nxt.append(target)
		frontier = nxt

	return sorted(
		((distance, state) for state, distance in rank.items()),
		key=lambda pair: (pair[0], order.get(pair[1], len(order))),
	)


def _drop_detours(edges: dict[str, list[str]], ranked: list[tuple[int, str]]) -> list[tuple[int, str]]:
	"""Remove the excursions: states that only ever lead back to where they came from.

	`Disputed` on a reimbursement list is the one FoLT has. It is reachable, submitted, and has a
	way out, so is_turn_down rightly says it is not a rejection -- but its only exit is back to
	`Paid`, so it is not progress either. Drawn as the last step it would read as though every
	list ends up disputed; and it would leave the workflow with no terminal step at all, because
	`Paid -> Disputed -> Paid` means neither of them is an end.

	A state with no exits at all is not a detour -- that is a finished document (`Approved`,
	`Closed`, `Settled`), which is why having at least one progress edge is part of the test.
	"""
	rank_of = {state: rank for rank, state in ranked}
	detours = {
		state
		for rank, state in ranked
		if edges.get(state)
		and all(rank_of.get(target, rank + 1) <= rank for target in edges[state])
	}

	return [(rank, state) for rank, state in ranked if state not in detours]


def _lanes(workflow, ranked: list[tuple[int, str]], optional: set[str]) -> list[dict]:
	"""Group the ranked states into the steps a tracker draws.

	Two states at the same rank are one step, because that is what they are to the person
	reading it. Optional states are carried on the lane they lead into rather than given a step
	of their own: a float at `Overdue` is at the accounting step and late, not at a step between
	disbursement and accounting.
	"""
	edges = _forward_edges(workflow)
	on_spine = {state for _, state in ranked}
	by_rank: dict[int, list[str]] = {}
	for rank, state in ranked:
		by_rank.setdefault(rank, []).append(state)

	# An optional state belongs to the first spine state it can reach: that is the step it is on
	# the way to, and the step a document sitting in it is waiting to get to.
	attached: dict[int, list[str]] = {}
	rank_of = {state: rank for rank, state in ranked}
	for state in sorted(optional):
		landings = [rank_of[t] for t in _reaches(edges, state) if t in on_spine]
		if landings:
			attached.setdefault(min(landings), []).append(state)

	lanes = []
	for i, rank in enumerate(sorted(by_rank)):
		group = by_rank[rank]
		extra = attached.get(rank, [])
		members = group + extra
		movers = sorted({role for state in group for role in _movers(workflow, state)})
		custodians = sorted(
			{row.allow_edit for row in workflow.states if row.state in members and row.allow_edit}
		)
		lanes.append(
			{
				"rank": i,
				"label": " / ".join(group),
				"states": group,
				# States that land a document on this step without being the step itself. A
				# tracker shows these as a note on the step, not as a step.
				"optional": extra,
				# Who moves it on from here -- which is what a step in a tracker is asking. The
				# custodian is the different question of who may edit it while it sits here, and
				# it is reported separately because enforce_state_custodian makes it a real rule
				# and somebody told "not your step" deserves to have been able to see it coming.
				"roles": movers,
				"custodian": custodians,
				"docstatus": max(
					(int(row.doc_status or 0) for row in workflow.states if row.state in members),
					default=0,
				),
				# Nothing leads on from here to a step that is still on the path. Measured
				# against the spine so a document parked at `Paid` reads as finished even though
				# `Raise Dispute` is technically still offered.
				"terminal": not any(
					target in on_spine and rank_of[target] > rank
					for state in group
					for target in edges.get(state, [])
				),
			}
		)

	return lanes


def _off_path(workflow, states: list[str], on_path: set[str], first: str | None) -> dict[str, dict]:
	"""The states a tracker does not draw as steps, and why each one is not a step.

	`turned_down` states are reachable and meaningful -- they are where a rejected document
	lives -- so a caller renders them beside the tracker rather than in it. `unreachable` means
	the workflow has a state nothing leads to, which is a fixture bug rather than a design, and
	worth reporting rather than hiding.
	"""
	off = {}
	for state in states:
		if state in on_path:
			continue

		reachable = any(
			row.next_state == state for row in workflow.transitions if row.state != state
		)
		turned_down = first is not None and any(
			is_turn_down(workflow, row.state, state)
			for row in workflow.transitions
			if row.next_state == state
		)

		off[state] = {
			"kind": "turned_down" if turned_down else ("detour" if reachable else "unreachable"),
			"roles": _movers(workflow, state),
			"custodian": next(
				(row.allow_edit for row in workflow.states if row.state == state and row.allow_edit),
				None,
			),
		}

	return off


def _movers(workflow, state: str) -> list[str]:
	"""The roles that can move a document on from `state` -- progress only, not turn-downs.

	Deliberately narrower than workflow.get_approvers_for_state, which answers "who can act on
	this" and counts a rejection as acting. Both are wanted: this labels the step, that one
	names the people to chase.
	"""
	return sorted(
		{
			row.allowed
			for row in workflow.transitions
			if row.state == state
			and row.allowed
			and row.next_state != state
			and not is_turn_down(workflow, row.state, row.next_state)
		}
	)


# --- position of one document -----------------------------------------------------------


def locate(shaped: dict, state: str | None) -> dict:
	"""Where `state` sits in a shape: which lane is current, and what is done or still ahead.

	Split from shape() because shape() is per workflow and this is per document -- the split is
	what lets the Desk be handed all nine shapes once at boot and then place a document against
	them without another round trip.
	"""
	lanes = shaped["lanes"]
	# An optional state places the document on the lane it leads into: a float at `Overdue` is
	# waiting to be accounted for, so the accounting step is where it is.
	current = next(
		(lane for lane in lanes if state and state in lane["states"] + lane["optional"]), None
	)
	off = shaped["off_path"].get(state) if state else None

	return {
		"state": state,
		"lane": current["rank"] if current else None,
		"of": len(lanes),
		# Set when the document is at an optional state rather than the step proper, so a caller
		# can say "at the accounting step, overdue" rather than silently rounding it to the step.
		"at_optional": state if current and state in current["optional"] else None,
		# A document sitting off the path is not at any step, and saying which step it "would" be
		# at would be an invention. What it is, is turned down -- and the caller says so.
		"off_path": dict(off, state=state) if off else None,
		"steps": [
			dict(lane, status=_status(lane, current, off))
			for lane in lanes
		],
	}


def _status(lane: dict, current: dict | None, off: dict | None) -> str:
	if current and lane["rank"] == current["rank"]:
		return "current"
	if current and lane["rank"] < current["rank"]:
		return "done"
	if off and off["kind"] == "turned_down":
		# Everything is ahead of a turned-down document, including the step it came from: it has
		# to be picked up and moved through them again.
		return "ahead"
	if current:
		return "ahead"
	return "ahead"


# --- audit ------------------------------------------------------------------------------


def shape_audit(verbose: bool = True) -> dict:
	"""Print the derived step plan for every active workflow, so it can be read and checked.

	The derivation is a heuristic over a graph, and the failure mode is a tracker that is subtly
	wrong rather than one that breaks -- so it is worth being able to see what it decided without
	driving a document into every state. Run it after any change to fixtures/workflow.json:

	    bench --site folt.localhost execute folt_customizations.workflow_shape.shape_audit

	Anything that reads wrongly is fixed by adding a STEP_PLAN entry, not by editing _rank.
	"""
	report = {}
	for name in sorted(frappe.get_all("Workflow", filters={"is_active": 1}, pluck="name")):
		workflow = frappe.get_cached_doc("Workflow", name)
		shaped = _shape_of(workflow)
		report[name] = shaped

		if not verbose:
			continue

		source = "STEP_PLAN" if name in STEP_PLAN else "derived"
		print(f"\n{name}  ({shaped['doctype']}, {source})")
		for lane in shaped["lanes"]:
			flags = []
			if lane["docstatus"] == 1:
				flags.append("submitted")
			if lane["docstatus"] == 2:
				flags.append("cancelled")
			if lane["terminal"]:
				flags.append("terminal")
			suffix = f"  [{', '.join(flags)}]" if flags else ""
			movers = ", ".join(lane["roles"]) or "nobody"
			optional = f"  (or {', '.join(lane['optional'])})" if lane["optional"] else ""
			print(f"  {lane['rank'] + 1}. {lane['label']}{optional}  -> {movers}{suffix}")

		for state, info in shaped["off_path"].items():
			print(f"     off path: {state} ({info['kind']})")

	return report
