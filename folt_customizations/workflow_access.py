"""Who may touch a FoLT document at each step of its workflow -- enforced, not merely displayed.

Every FoLT approval chain already states who owns each step. `fixtures/workflow.json` gives each
state an `allow_edit` role (Draft belongs to the requester, `Pending Head of Programs` to the
Head of Programs, `Disbursed` to the Finance Assistant, and so on) and each transition a list of
roles `allowed` to make the move. Between them those two fields are the map of roles onto
workflows -- nine workflows, thirty role/doctype pairs.

HALF OF THAT MAP WAS DECORATION. Frappe enforces the transitions and not the states.
`frappe.model.workflow.validate_workflow` -- the only server-side workflow check there is --
looks up the current state's row and then uses it for nothing but a spelling check; the
`allow_edit` role is read in exactly one place in the whole framework,
`public/js/frappe/model/workflow.js:is_read_only`, which greys the field out in the Desk form.
So the form said read-only and the server said yes to anybody holding plain `write` on the
doctype: the REST API, `frappe.client.set_value`, a list-view inline edit, a bulk edit, a report
edit, or any script.

What that cost, concretely, is a requisition sitting at `Pending Head of Programs` whose
requester -- allowed to edit only in `Draft` -- can still raise its own budget, so the approver
signs a figure they never saw. Every workflow here has the same shape, and the states after
submission (`Disbursed`, `Accounted`, `Paid`, `Settled`) are worse, because nothing else guards
them either.

`enforce_state_custodian` closes that: a document may only be changed by the role the workflow
says holds it at the step it is on. With that in place, the doctype permissions go back to being
the coarse outer gate they are good at being -- a role can hold `write` on Purchase Order
without being able to touch one that has left the buyer's hands -- which is why this fixes the
role-to-workflow mapping without revoking a single standard ERPNext or HRMS permission.

`workflow_role_map` and `audit` are the other half: they derive what each workflow *requires*
from the workflows themselves and check the granted permissions against it, so the table in
permissions.py cannot drift away from the chains it exists to serve.
"""

import frappe
from frappe import _
from frappe.model.workflow import get_workflow, get_workflow_name

# Saves that are the system's own rather than a person's: fixtures, patches, the participant and
# reimbursement builders, the e2e scripts. All of them already say `ignore_permissions`, which is
# the same statement -- this code is acting for FoLT, not for whoever happens to be logged in.
SYSTEM_FLAGS = ("in_install", "in_migrate", "in_patch", "in_import", "in_test")

# The field every workflow-governed doctype carries to hold the reason it was turned down: on
# FoLT's own five it is in the doctype json, on Purchase Order, Employee Advance, Salary Slip and
# Expense Claim it is a Custom Field fixture. `read_only` on all nine -- it is written by
# require_rejection_reason from what the person typed in the dialog, never edited in the form
# afterwards, so what the record says is what they were told.
REJECTION_REASON_FIELD = "folt_rejection_reason"

# How long a typed reason waits for the action it belongs to. Long enough for the round trip from
# the dialog to the workflow button, short enough that it is never a place state lives.
REASON_HOLD_SECONDS = 120


def enforce_state_custodian(doc, method=None):
	"""Refuse a change to a workflow document by anyone but the role holding its current step.

	Hooked on `validate` *and* `before_update_after_submit` for every doctype (hooks.doc_events
	"*"), because those are two different code paths and only one of them runs per save: Frappe
	skips `validate` entirely when a submitted document is edited and runs
	`before_update_after_submit` instead (document.py:run_before_save_methods). Hooking only
	`validate` would have left every state after submission -- `Disbursed`, `Accounted`, `Paid`,
	`Settled` -- exactly as open as before.

	Three things are deliberately let through:

	*Transitions.* When the workflow state itself changes, this steps aside and lets Frappe check
	the transition's own `allowed` roles, because the mover is frequently not the custodian --
	the Executive Director approves an Employee Advance out of `Checked`, a state that belongs to
	the Finance Officer. The corollary is that a save which makes a valid transition may carry
	other edits with it. That is inherent to the way Frappe models an approval as a document
	save, and it is the approver's own edit on their own step, not somebody reaching into it.

	*Inserts.* A new document is at its first state with nothing approved yet, so the question of
	who owns the step has not arisen; `create` on the doctype is the right gate there, and
	enforcing the first state's `allow_edit` on top of it would only lock out the standard roles
	that legitimately raise these documents.

	*A state with no `allow_edit`.* Read as "unrestricted", not "nobody" -- a workflow with a
	blank state should not become a document nobody can save. Every FoLT state names a role, so
	this only matters for a workflow added later and half filled in.
	"""
	if not _is_a_person_editing(doc):
		return

	workflow_name = get_workflow_name(doc.doctype)
	if not workflow_name:
		return

	before = doc.get_doc_before_save()
	if not before:
		return

	workflow = get_workflow(doc.doctype)
	current_state = before.get(workflow.workflow_state_field)
	if not current_state or current_state != doc.get(workflow.workflow_state_field):
		return

	custodians = [row.allow_edit for row in workflow.states if row.state == current_state and row.allow_edit]
	if not custodians or set(custodians) & set(frappe.get_roles()):
		return

	frappe.throw(
		_("{0} {1} is at {2}, and only {3} may change it at that step. Ask them to make the change, or move it on first.").format(
			_(doc.doctype), frappe.bold(doc.name), frappe.bold(_(current_state)), frappe.bold(", ".join(custodians))
		),
		title=_("Not your step"),
	)


def _is_a_person_editing(doc) -> bool:
	return _is_a_person_acting(doc) and frappe.session.user != "Administrator"


def _is_a_person_acting(doc) -> bool:
	"""Whether this save is somebody's edit rather than FoLT's own code running.

	Administrator is *not* excused here, only in _is_a_person_editing: which step a document is on
	is a question about a person's role, and the administrator has every role, but why a document
	was turned down is a question about the document, and the answer is missing whoever asked it.
	"""
	if doc.flags.ignore_permissions:
		return False
	return not any(frappe.flags.get(flag) for flag in SYSTEM_FLAGS)


# --- turning a document down --------------------------------------------------------------


def is_turn_down(workflow, from_state: str, to_state: str) -> bool:
	"""Whether this move turns a document down rather than moving it along.

	Derived from the shape of the workflow rather than from the wording of the action, because the
	wording is the part that drifts -- FoLT's chains say Reject in seven places and Return for
	Correction in four, and the eighth chain to be written will say something else. Two shapes
	count, and between them they pick out exactly the fifteen transitions FoLT has:

	  - back to the state the document starts in. Somebody has been asked to do it again, and the
	    only useful thing they can be told is what to change.
	  - into an unsubmitted state with no way out of it. The document is finished and was not
	    approved, and the reason is the whole of what is left of it.

	`Paid -> Disputed` on a reimbursement list is deliberately neither: it is submitted, and it
	has `Resolve Dispute` leading out of it, so it is a thread that continues rather than a
	document turned down. If FoLT wants a reason on a dispute too, that is a third shape here and
	a field that already exists, not a rewrite.
	"""
	if from_state == to_state:
		return False
	if to_state == workflow.states[0].state:
		return True

	target = next((row for row in workflow.states if row.state == to_state), None)
	if not target or int(target.doc_status or 0) != 0:
		return False
	return not any(row.state == to_state for row in workflow.transitions)


def require_rejection_reason(doc, method=None):
	"""A document turned down or sent back has to say why. Hooked on the same two save paths.

	This is what FoLT gets instead of a guard on cancellation. A cancel takes a document out of
	the chain and says nothing to anybody; a rejection is a step *in* the chain, aimed at a named
	person who now has to act, and the one thing they need is the reason. So the reason is not
	optional: no transition of either turn-down shape completes without one, whether it comes
	from the dialog on the form, from a script, or from the API.

	The same pass clears the field on the way back out. A requisition returned for correction,
	fixed and resubmitted must not still be carrying last week's complaint -- and clearing it also
	means the next rejection cannot quietly ride on the last one's wording. Nothing is lost:
	record_rejection_reason has already put each one in the timeline.
	"""
	workflow_name = get_workflow_name(doc.doctype)
	if not workflow_name or not doc.meta.has_field(REJECTION_REASON_FIELD):
		return

	before = doc.get_doc_before_save()
	if not before:
		return

	workflow = get_workflow(doc.doctype)
	from_state = before.get(workflow.workflow_state_field)
	to_state = doc.get(workflow.workflow_state_field)
	if from_state == to_state:
		return

	if not is_turn_down(workflow, from_state, to_state):
		# Moving on, not down: last time's reason does not travel with it.
		doc.set(REJECTION_REASON_FIELD, None)
		return

	reason = (held_reason(doc.doctype, doc.name, before.modified) or doc.get(REJECTION_REASON_FIELD) or "").strip()
	if not reason and _is_a_person_acting(doc):
		frappe.throw(
			_("Say why: {0} cannot be sent to {1} without a reason, because somebody has to act on it.").format(
				frappe.bold(doc.name), frappe.bold(_(to_state))
			),
			title=_("A reason is required"),
		)

	doc.set(REJECTION_REASON_FIELD, reason or None)


def record_rejection_reason(doc, method=None):
	"""Put the reason in the timeline, beside the state change it explains.

	The field only ever holds the current one -- require_rejection_reason clears it when the
	document moves on -- so the timeline is where the history lives. frappe's own workflow comment
	names the state and nothing else (`apply_workflow` -> add_comment("Workflow", next_state)), so
	this reads as the second half of the same entry rather than a duplicate of it.
	"""
	reason = (doc.get(REJECTION_REASON_FIELD) or "").strip()
	if not reason:
		return

	before = doc.get_doc_before_save()
	workflow_name = get_workflow_name(doc.doctype)
	if not before or not workflow_name:
		return

	workflow = get_workflow(doc.doctype)
	from_state = before.get(workflow.workflow_state_field)
	to_state = doc.get(workflow.workflow_state_field)
	if not is_turn_down(workflow, from_state, to_state):
		return

	doc.add_comment("Workflow", _("{0} — {1}").format(_(to_state), reason))
	forget_held_reason(doc.doctype, doc.name, before.modified)


def add_turn_downs_to_bootinfo(bootinfo):
	"""Tell the Desk which workflow actions turn a document down. `extend_bootinfo` hook.

	The form script has to know before a form is opened, and the answer is derived from the
	workflows (is_turn_down), so it is sent with the boot payload rather than hard-coded in the
	JS -- a chain that gains a Reject next month gains the dialog with it, and there is no second
	list to keep in step. Small: fifteen pairs across nine doctypes.
	"""
	turn_downs = {}
	for name in frappe.get_all("Workflow", filters={"is_active": 1}, pluck="name"):
		workflow = frappe.get_cached_doc("Workflow", name)
		pairs = [
			[row.state, row.action]
			for row in workflow.transitions
			if is_turn_down(workflow, row.state, row.next_state)
		]
		if pairs:
			turn_downs[workflow.document_type] = {
				"state_field": workflow.workflow_state_field,
				"turn_downs": pairs,
			}

	bootinfo.folt_turn_downs = turn_downs


@frappe.whitelist()
def hold_rejection_reason(doctype: str, name: str, reason: str) -> None:
	"""Park the reason somebody just typed, for the workflow action that follows it.

	WHY A HAND-OFF AND NOT SIMPLY THE FORM FIELD. The workflow button posts its action to
	`frappe.model.workflow.apply_workflow`, which does `doc.load_from_db()` and throws away
	everything the browser sent bar the document's identity -- so a reason typed into the form
	cannot ride along with the action. Saving it first does not work either: the person turning a
	document down is not always the custodian of the step it is on (a Finance Officer rejects an
	Employee Advance out of `Requested`, which belongs to the requester), so that save is refused
	by enforce_state_custodian, correctly. Parking it for the length of one action is what lets
	the reason and the new state land in the same save, carried by the transition's own authority.

	Keyed by user as well as by document, so two approvers looking at the same document cannot
	pick up each other's wording, and short-lived, so this is never somewhere state lives. It is not consumed
	when it is read but when the save it belonged to has actually stuck
	(record_rejection_reason), so a save that fails validation further on can be retried without
	sending the person back through the dialog, and no later action can pick up a reason that was
	not typed for it.
	"""
	reason = (reason or "").strip()
	if not reason:
		frappe.throw(_("Say why this is being turned down."), title=_("A reason is required"))

	frappe.has_permission(doctype, "write", doc=name, throw=True)
	frappe.cache.set_value(
		_reason_key(doctype, name, frappe.db.get_value(doctype, name, "modified")),
		reason,
		user=frappe.session.user,
		expires_in_sec=REASON_HOLD_SECONDS,
	)


def held_reason(doctype: str, name: str, modified) -> str | None:
	return frappe.cache.get_value(_reason_key(doctype, name, modified), user=frappe.session.user)


def forget_held_reason(doctype: str, name: str, modified) -> None:
	frappe.cache.delete_value(_reason_key(doctype, name, modified), user=frappe.session.user)


def _reason_key(doctype: str, name: str, modified) -> str:
	"""Keyed by the version of the document the reason was typed against, not just its name.

	`modified` is what ties the wording to the thing it was written about. Without it a reason
	left behind by an action that never completed can be picked up later by a different action --
	or, on a site where document names get reused, by a different document altogether, which is
	how this was found. If somebody else saves the document between the dialog and the button the
	key no longer matches, and being asked again is the right answer rather than a wrong reason
	being filed silently.
	"""
	return f"folt_rejection_reason:{doctype}:{name}:{modified}"


# --- the map, derived from the workflows themselves -----------------------------------------


def workflow_role_map() -> dict[tuple[str, str], set[str]]:
	"""{(doctype, role): {ptypes}} -- what each active workflow requires of each role it names.

	Derived rather than listed, so it cannot fall out of step with the chains it describes. The
	four rules are Frappe's, not FoLT's:

	  - anyone the workflow names needs `read`, and `write` to save at all;
	  - a transition that *ends* in docstatus 1 is a submit;
	  - a save on a document already at docstatus 1 is an update-after-submit, which Frappe
	    checks as the `submit` ptype (document.py:check_docstatus_transition) -- so a role that
	    only ever moves a submitted document between states still needs `submit`;
	  - the role that owns the first state is the one that raises the document, so it needs
	    `create`.
	"""
	required: dict[tuple[str, str], set[str]] = {}

	def require(doctype, role, *ptypes):
		if role:
			required.setdefault((doctype, role), set()).update(ptypes)

	for name in frappe.get_all("Workflow", filters={"is_active": 1}, pluck="name"):
		workflow = frappe.get_doc("Workflow", name)
		docstatus = {row.state: int(row.doc_status or 0) for row in workflow.states}
		first_state = workflow.states[0].state if workflow.states else None

		for row in workflow.states:
			if not row.allow_edit:
				continue
			require(workflow.document_type, row.allow_edit, "read", "write")
			if docstatus.get(row.state) == 1:
				require(workflow.document_type, row.allow_edit, "submit")
			if row.state == first_state:
				require(workflow.document_type, row.allow_edit, "create")

		for row in workflow.transitions:
			for role in (row.allowed or "").split("\n"):
				require(workflow.document_type, role.strip(), "read", "write")
				if 1 in (docstatus.get(row.state), docstatus.get(row.next_state)):
					require(workflow.document_type, role.strip(), "submit")

	return required


def granted(doctype: str, role: str, permlevel: int = 0):
	"""The rule actually in force for `role`, read through Meta so Custom DocPerm wins where it
	exists -- the same way permissions.py reads it."""
	for perm in frappe.get_meta(doctype).permissions:
		if perm.role == role and perm.permlevel == permlevel and not perm.if_owner:
			return perm


def audit(verbose: bool = True) -> dict:
	"""Check the granted permissions against what the workflows require, both directions.

	Run with `bench --site <site> execute folt_customizations.workflow_access.audit`.

	MISSING is a break: a role the workflow asks to act that cannot. Each one shows up in
	practice as a bare PermissionError halfway through an approval, so this is the list to keep
	empty -- permissions.py:apply_role_permissions exists to keep it that way.

	UNMAPPED is not a break, and is reported rather than fixed: a role holding `write` or
	`submit` on a doctype some workflow governs, without appearing anywhere in that workflow.
	Most of them are ERPNext's and HRMS's own roles, and revoking them would be picking a fight
	with every future app update. enforce_state_custodian is what makes them harmless -- the
	permission lets them open the list, the workflow still decides who may change a document at
	the step it is on.
	"""
	required = workflow_role_map()
	governed = {doctype for doctype, _role in required}

	missing, unmapped = [], []

	for (doctype, role), ptypes in sorted(required.items()):
		perm = granted(doctype, role)
		absent = sorted(ptype for ptype in ptypes if not (perm and perm.get(ptype)))
		if absent:
			missing.append({"doctype": doctype, "role": role, "ptypes": absent})

	named = set(required)
	for doctype in sorted(governed):
		for perm in frappe.get_meta(doctype).permissions:
			if perm.permlevel or not (perm.get("write") or perm.get("submit")):
				continue
			if (doctype, perm.role) in named or perm.role in ("System Manager", "Administrator"):
				continue
			unmapped.append({"doctype": doctype, "role": perm.role})

	if verbose:
		print(f"\n{len(required)} role/doctype pairs across {len(governed)} workflow-governed doctypes\n")
		print(f"  MISSING -- named by a workflow, cannot do it ({len(missing)}):")
		for row in missing or [None]:
			print(f"    {row['doctype']} / {row['role']} -- needs {', '.join(row['ptypes'])}" if row else "    none")
		print(f"\n  UNMAPPED -- can write, no step to their name ({len(unmapped)}):")
		for row in unmapped or [None]:
			print(f"    {row['doctype']} / {row['role']}" if row else "    none")
		print("\n  Unmapped roles cannot change a document at a step that is not theirs --")
		print("  workflow_access.enforce_state_custodian holds them to the workflow.\n")

	return {"required": len(required), "missing": missing, "unmapped": unmapped}
