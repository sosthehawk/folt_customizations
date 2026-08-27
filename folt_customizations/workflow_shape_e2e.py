"""End-to-end check that every FoLT workflow reads as a sensible sequence of steps.

workflow_shape derives a tracker from a Workflow definition, and the thing about a derivation
over a graph is that it does not fail loudly when it is wrong. It produces a tracker that is
subtly off -- a step in the wrong place, an exception state drawn as though every document
passes through it, a chain with no end -- and nobody reports that, because it looks like a
design decision.

So this asserts the properties the derivation is supposed to have, over all nine live workflows
rather than over an example. Three of them are worth naming:

  - `Rejected` is never a step. That is the check that says workflow_access.is_turn_down is
    still being consulted; if it ever stops being, every tracker in FoLT grows a "Rejected" step
    at the end and reads as though rejection were the last stage of approval.
  - the states partition. Every state a workflow has is either a step, optional, or off the
    path, exactly once -- so no state can be silently dropped and invisible in the Desk.
  - the reimbursement list terminates. `Paid -> Raise Dispute -> Disputed -> Resolve Dispute ->
    Paid` is a genuine cycle in the graph, and it is the case that would hang a longest-path
    implementation or leave the workflow with no end state.

Reads only: no fixtures, no documents, nothing to tear down, so it is safe on any site and
fast enough to run on every change. Run with

    bench --site <site> execute folt_customizations.workflow_shape_e2e.run
"""

import frappe

from folt_customizations.workflow_shape import STEP_PLAN, locate, shape

PASS, FAIL = [], []


def check(label, condition, detail=""):
	(PASS if condition else FAIL).append(label)
	print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")


def active_workflows():
	return sorted(frappe.get_all("Workflow", filters={"is_active": 1}, pluck="name"))


def run():
	print("\n=== every workflow reads as a sequence of steps ===\n")

	workflows = active_workflows()
	check("there are active workflows to check", bool(workflows), f"{len(workflows)} found")
	if not workflows:
		raise SystemExit(1)

	for name in workflows:
		workflow = frappe.get_cached_doc("Workflow", name)
		shaped = shape(workflow.document_type)
		print(f"\n--- {name} ({workflow.document_type}) ---")

		if not shaped:
			check(f"{name}: has a shape", False, "shape() returned None for a doctype with a workflow")
			continue

		lanes = shaped["lanes"]
		declared = [row.state for row in workflow.states]

		check(f"{name}: is a chain, not a single state", len(lanes) >= 2, f"{len(lanes)} steps")

		check(
			f"{name}: starts where the workflow starts",
			bool(lanes) and declared[0] in lanes[0]["states"],
			f"step 1 is {lanes[0]['label'] if lanes else 'nothing'}, workflow starts at {declared[0]}",
		)

		check(
			f"{name}: step numbers run 0..n-1 with no gaps",
			[lane["rank"] for lane in lanes] == list(range(len(lanes))),
			str([lane["rank"] for lane in lanes]),
		)

		# The partition. Every state the workflow declares must be accounted for exactly once,
		# or the Desk has a state it cannot draw and a document that lands in it disappears.
		placed = [state for lane in lanes for state in lane["states"] + lane["optional"]]
		placed += list(shaped["off_path"])
		duplicated = sorted({state for state in placed if placed.count(state) > 1})
		missing = sorted(set(declared) - set(placed))
		invented = sorted(set(placed) - set(declared))
		check(
			f"{name}: every state is placed exactly once",
			not duplicated and not missing and not invented,
			f"duplicated={duplicated} missing={missing} invented={invented}",
		)

		# The is_turn_down regression net.
		turned_down = sorted(
			state for state, info in shaped["off_path"].items() if info["kind"] == "turned_down"
		)
		step_states = {state for lane in lanes for state in lane["states"] + lane["optional"]}
		check(
			f"{name}: no turned-down state is drawn as a step",
			not (set(turned_down) & step_states),
			f"turned down: {turned_down or 'none'}",
		)

		check(
			f"{name}: the chain has an end",
			bool(shaped["terminal"]),
			f"terminal: {shaped['terminal']}",
		)

		# A step nobody can move a document out of, that is not the end, is a stuck chain.
		stranded = [
			lane["label"]
			for lane in lanes
			if not lane["roles"] and not lane["terminal"]
		]
		check(f"{name}: no step is a dead end", not stranded, f"stranded: {stranded}")

		check(
			f"{name}: no state is left unreachable",
			not [s for s, i in shaped["off_path"].items() if i["kind"] == "unreachable"],
			str([s for s, i in shaped["off_path"].items() if i["kind"] == "unreachable"]),
		)

		# Same input, same answer -- the derivation sorts by fixture order precisely so that a
		# tracker does not reshuffle itself between two page loads.
		check(
			f"{name}: is deterministic",
			shape(workflow.document_type) == shaped,
			"two calls agree",
		)

	print("\n--- the shapes FoLT's own chains are supposed to have ---")

	expected = {
		"Activity Requisition": (4, ["Approved"]),
		"Activity Participant List": (3, ["Verified"]),
		"Participant Reimbursement List": (5, ["Paid"]),
		"Procurement Committee Evaluation": (4, ["Approved"]),
		"Derogation Waiver Request": (4, ["Approved"]),
		"Purchase Order": (3, ["Approved"]),
		"Employee Advance": (6, ["Closed"]),
		"Salary Slip": (3, ["Approved"]),
		"Expense Claim": (5, ["Settled"]),
	}
	for doctype, (steps, terminal) in expected.items():
		shaped = shape(doctype)
		check(
			f"{doctype}: {steps} steps ending at {', '.join(terminal)}",
			bool(shaped) and len(shaped["lanes"]) == steps and shaped["terminal"] == terminal,
			f"got {len(shaped['lanes']) if shaped else 0} steps ending at "
			f"{shaped['terminal'] if shaped else None}",
		)

	print("\n--- the states a document does not have to pass through ---")

	# The three optional states FoLT has, and why each is optional rather than a step: in every
	# case the workflow also offers the way round it. Asserted by name because getting one of
	# these wrong is exactly the kind of quiet mis-read this driver exists to catch -- drawing
	# `Overdue` as step five would tell every reader that every float goes overdue.
	optional_by_doctype = {
		"Employee Advance": ["Overdue"],
		"Participant Reimbursement List": ["Partly Paid"],
		"Derogation Waiver Request": ["Pending Committee Review"],
	}
	for doctype, states in optional_by_doctype.items():
		shaped = shape(doctype)
		found = sorted(state for lane in shaped["lanes"] for state in lane["optional"])
		check(
			f"{doctype}: {', '.join(states)} is optional, not a step",
			found == sorted(states),
			f"optional: {found}",
		)

	# The cycle. Left unhandled this is either an infinite loop or a chain with no end.
	prl = shape("Participant Reimbursement List")
	check(
		"a disputed reimbursement list is an excursion, not the last step",
		prl["off_path"].get("Disputed", {}).get("kind") == "detour",
		f"Disputed: {prl['off_path'].get('Disputed')}",
	)
	check(
		"and the list still ends at Paid despite the dispute cycle",
		prl["terminal"] == ["Paid"],
		f"terminal: {prl['terminal']}",
	)

	print("\n--- placing a document against a shape ---")

	advance = shape("Employee Advance")

	at_checked = locate(advance, "Checked")
	check(
		"a float at Checked is on step 2 of 6",
		(at_checked["lane"], at_checked["of"]) == (1, 6),
		f"lane {at_checked['lane']} of {at_checked['of']}",
	)
	check(
		"with the earlier steps done and the later ones ahead",
		[step["status"] for step in at_checked["steps"]]
		== ["done", "current", "ahead", "ahead", "ahead", "ahead"],
		str([step["status"] for step in at_checked["steps"]]),
	)

	# An optional state is not a step of its own, so a float that has gone overdue has to be
	# placed somewhere -- and the honest answer is the step it is waiting to reach.
	at_overdue = locate(advance, "Overdue")
	check(
		"an overdue float is at the accounting step, and says it is overdue",
		at_overdue["lane"] == 4 and at_overdue["at_optional"] == "Overdue",
		f"lane {at_overdue['lane']}, at_optional {at_overdue['at_optional']}",
	)

	rejected = locate(advance, "Rejected")
	check(
		"a rejected float is at no step, and is reported as turned down",
		rejected["lane"] is None
		and (rejected["off_path"] or {}).get("kind") == "turned_down",
		f"lane {rejected['lane']}, off_path {rejected['off_path']}",
	)

	blank = locate(advance, None)
	check(
		"a document with no state yet is at no step rather than an error",
		blank["lane"] is None and len(blank["steps"]) == 6,
		f"lane {blank['lane']}, {len(blank['steps'])} steps",
	)

	unknown = locate(advance, "Not A State")
	check(
		"a state the workflow has never heard of is placed nowhere rather than guessed at",
		unknown["lane"] is None and unknown["off_path"] is None,
		f"lane {unknown['lane']}, off_path {unknown['off_path']}",
	)

	print("\n--- doctypes with no workflow ---")

	check(
		"a doctype with no workflow has no shape, and says so rather than throwing",
		shape("User") is None,
	)

	print("\n--- and the derivation is still doing the work ---")

	# STEP_PLAN is an escape hatch, and an entry in it is a workflow the derivation could not
	# read. That is worth noticing rather than accumulating quietly: the point of this module is
	# that trackers come from the workflows, and every override is one that does not.
	check(
		"no workflow needs its steps written out by hand",
		not STEP_PLAN,
		f"overridden: {sorted(STEP_PLAN)}" if STEP_PLAN else "all nine derive",
	)

	print(f"\n  {len(PASS)} passed, {len(FAIL)} failed")
	if FAIL:
		print("  FAILED: " + "; ".join(FAIL))
		raise SystemExit(1)
	return {"passed": len(PASS), "failed": len(FAIL), "failures": FAIL}
