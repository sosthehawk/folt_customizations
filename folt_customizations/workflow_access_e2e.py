"""End-to-end check that FoLT's roles map onto the workflow steps they are meant to own.

Two claims, and they fail in opposite directions.

The first is that every role a workflow names can actually do what it is asked to do. A missing
permission here does not show up as a locked button -- it shows up as a bare PermissionError
partway through somebody's approval, on a document they can see and are being chased about. The
required set is derived from the workflows themselves (workflow_access.workflow_role_map), so
this stays honest when a chain changes.

The second is that a role can only act on a document at the step it holds. That one was not true
at all: Frappe enforces a workflow's transitions and ignores its `allow_edit`, which is read
nowhere on the server -- so a requisition sitting with the Head of Programs could still be
rewritten by the requester who raised it. workflow_access.enforce_state_custodian is the rule;
the checks below are what say it is switched on and pointed at the right people.

Idempotent: tears down its own fixtures first. Run with

    bench --site <site> execute folt_customizations.workflow_access_e2e.run
"""

import frappe
from frappe.model.workflow import apply_workflow
from frappe.utils import nowdate

from folt_customizations.workflow_access import (
	REJECTION_REASON_FIELD,
	audit,
	hold_rejection_reason,
	is_turn_down,
	workflow_role_map,
)

PROGRAM = "E2E Workflow Custody"
ACTIVITY = "E2E Workflow Custody Activity"

# The seeded role logins rather than users made here, the same way finance_workflow_e2e works.
# One of them has to hold the Employee role, and that role cannot simply be granted: ERPNext
# strips it from any User with no Employee record behind it
# (erpnext.setup.doctype.employee.employee.validate_employee_role, hooked on User.validate). The
# seeder builds the Employee record too, so this asks for its users instead of half-making them.
REQUESTER = "requester.test@folt.test"
PROGRAMS_HEAD = "hop.test@folt.test"
FINANCE_HEAD = "hof.test@folt.test"

HANDLER = "folt_customizations.workflow_access.enforce_state_custodian"

PASS, FAIL = [], []


def check(label, condition, detail=""):
	(PASS if condition else FAIL).append(label)
	print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")


def expect_throw(label, fn):
	try:
		fn()
	except frappe.ValidationError as e:
		check(label, True, frappe.utils.strip_html(str(e)).strip()[:80].replace("\n", " "))
		return
	except Exception as e:  # noqa: BLE001
		check(label, False, f"wrong error type: {type(e).__name__}: {e}")
		return
	check(label, False, "no error raised")


def as_user(user, fn):
	previous = frappe.session.user
	frappe.set_user(user)
	try:
		return fn()
	finally:
		frappe.set_user(previous)


# --- fixtures --------------------------------------------------------------------------------


def teardown():
	for name in frappe.get_all("Activity Requisition", filters={"activity_program": PROGRAM}, pluck="name"):
		doc = frappe.get_doc("Activity Requisition", name)
		if doc.docstatus == 1:
			doc.cancel()
		frappe.delete_doc("Activity Requisition", name, force=True, ignore_permissions=True)

	activities = frappe.get_all("Project", filters={"project_name": ACTIVITY}, pluck="name")
	for name in frappe.get_all("Activity Participant List", filters={"activity": ("in", activities or [""])}, pluck="name"):
		doc = frappe.get_doc("Activity Participant List", name)
		if doc.docstatus == 1:
			doc.cancel()
		frappe.delete_doc("Activity Participant List", name, force=True, ignore_permissions=True)

	for name in activities:
		frappe.delete_doc("Project", name, force=True, ignore_permissions=True)
	frappe.db.commit()


def require_users():
	missing = [user for user in (REQUESTER, PROGRAMS_HEAD, FINANCE_HEAD) if not frappe.db.exists("User", user)]
	if missing:
		raise SystemExit(f"seed the role test users first (seed_test_users.py): missing {missing}")


def workflow_comments(doctype, name):
	return frappe.get_all(
		"Comment",
		filters={"reference_doctype": doctype, "reference_name": name, "comment_type": "Workflow"},
		pluck="content",
	)


def make_requisition(budget):
	"""A requisition with just enough on it to be saved.

	`float_required` is off: this suite is about who may change a document at which step, and a
	requisition with no cash component exercises that without dragging the float chain in. See
	activity_chain_e2e for the chain itself.
	"""
	return frappe.get_doc(
		{
			"doctype": "Activity Requisition",
			"activity_program": PROGRAM,
			"requested_by": frappe.db.get_value("Employee", {"user_id": REQUESTER, "status": "Active"})
			or frappe.get_all("Employee", filters={"status": "Active"}, pluck="name")[0],
			"activity_date": frappe.utils.nowdate(),
			"company": frappe.defaults.get_defaults().get("company")
			or frappe.get_all("Company", pluck="name")[0],
			"budget_amount": budget,
			"budget_line": "2.2.2",
			"float_required": 0,
		}
	).insert()


def edit_budget(name, amount):
	"""Change the requested figure -- the thing an approver is being asked to sign."""

	def _edit():
		doc = frappe.get_doc("Activity Requisition", name)
		doc.budget_amount = amount
		doc.save()

	return _edit


# --- checks ----------------------------------------------------------------------------------


def run():
	print("\nWorkflow access — every role can do its own step, and only its own step\n")
	frappe.set_user("Administrator")
	require_users()
	teardown()

	print("--- the map: what the workflows require of the permissions ---")

	required = workflow_role_map()
	report = audit(verbose=False)
	check(
		"every role a workflow names has the permissions its steps need",
		not report["missing"],
		"; ".join(f"{row['doctype']}/{row['role']} needs {','.join(row['ptypes'])}" for row in report["missing"]),
	)
	check(
		"the map is not empty -- the workflows are loaded and active",
		len(required) >= 25,
		f"{len(required)} role/doctype pairs across {len({dt for dt, _r in required})} doctypes",
	)

	stateless = [
		f"{wf}:{state}"
		for wf in frappe.get_all("Workflow", filters={"is_active": 1}, pluck="name")
		for state in frappe.get_all(
			"Workflow Document State", filters={"parent": wf, "allow_edit": ("in", ("", None))}, pluck="state"
		)
	]
	check(
		"every state of every active workflow names the role that holds it",
		not stateless,
		"; ".join(stateless),
	)

	print("\n--- the rule is switched on, on both save paths ---")

	hooks = frappe.get_hooks("doc_events").get("*", {})
	for event in ("validate", "before_update_after_submit"):
		check(
			f"the custodian rule is hooked on {event}",
			HANDLER in (hooks.get(event) or []),
			", ".join(hooks.get(event) or []) or "not hooked",
		)

	print("\n--- and it holds: a requisition belongs to the step it is on ---")

	requisition = as_user(REQUESTER, lambda: make_requisition(50_000))
	check("the requester raises it in Draft", requisition.workflow_state == "Draft", requisition.workflow_state)

	as_user(REQUESTER, edit_budget(requisition.name, 60_000))
	check(
		"and can revise it while it is still theirs",
		frappe.db.get_value("Activity Requisition", requisition.name, "budget_amount") == 60_000,
	)

	as_user(REQUESTER, lambda: apply_workflow(frappe.get_doc("Activity Requisition", requisition.name), "Submit for Review"))
	check(
		"submitting for review hands it to the Head of Programs",
		frappe.db.get_value("Activity Requisition", requisition.name, "workflow_state") == "Pending Head of Programs",
	)

	expect_throw(
		"the requester can no longer change the figure being approved",
		lambda: as_user(REQUESTER, edit_budget(requisition.name, 300_000)),
	)
	expect_throw(
		"nor can an approver further down the chain, before it reaches them",
		lambda: as_user(FINANCE_HEAD, edit_budget(requisition.name, 300_000)),
	)
	check(
		"the figure is untouched",
		frappe.db.get_value("Activity Requisition", requisition.name, "budget_amount") == 60_000,
	)

	as_user(PROGRAMS_HEAD, edit_budget(requisition.name, 55_000))
	check(
		"the Head of Programs, who holds this step, can",
		frappe.db.get_value("Activity Requisition", requisition.name, "budget_amount") == 55_000,
	)

	as_user(PROGRAMS_HEAD, lambda: apply_workflow(frappe.get_doc("Activity Requisition", requisition.name), "Approve"))
	check(
		"approving moves it on -- transitions are not blocked by the rule",
		frappe.db.get_value("Activity Requisition", requisition.name, "workflow_state") == "Pending Head of Finance",
	)

	as_user(FINANCE_HEAD, edit_budget(requisition.name, 52_000))
	check(
		"custody moved with it: now the Head of Finance can edit",
		frappe.db.get_value("Activity Requisition", requisition.name, "budget_amount") == 52_000,
	)
	expect_throw(
		"and the Head of Programs, whose step has passed, cannot",
		lambda: as_user(PROGRAMS_HEAD, edit_budget(requisition.name, 90_000)),
	)

	print("\n--- turning one down says why, and the reason reaches the next person ---")

	turned_down = as_user(REQUESTER, lambda: make_requisition(40_000))
	as_user(REQUESTER, lambda: apply_workflow(frappe.get_doc("Activity Requisition", turned_down.name), "Submit for Review"))

	expect_throw(
		"a rejection with no reason is refused",
		lambda: as_user(
			PROGRAMS_HEAD, lambda: apply_workflow(frappe.get_doc("Activity Requisition", turned_down.name), "Reject")
		),
	)
	check(
		"and it really did not happen",
		frappe.db.get_value("Activity Requisition", turned_down.name, "workflow_state") == "Pending Head of Programs",
	)

	REASON = "Budget line 2.2.2 has nothing left this quarter."
	as_user(
		PROGRAMS_HEAD,
		lambda: (
			hold_rejection_reason("Activity Requisition", turned_down.name, f"  {REASON}  "),
			apply_workflow(frappe.get_doc("Activity Requisition", turned_down.name), "Reject"),
		),
	)
	rejected = frappe.get_doc("Activity Requisition", turned_down.name)
	check("with a reason it goes through", rejected.workflow_state == "Rejected", rejected.workflow_state)
	check(
		"the reason is on the document, trimmed",
		rejected.get(REJECTION_REASON_FIELD) == REASON,
		repr(rejected.get(REJECTION_REASON_FIELD)),
	)
	check(
		"and in the timeline beside the state it explains",
		any(REASON in content for content in workflow_comments("Activity Requisition", turned_down.name)),
		"; ".join(workflow_comments("Activity Requisition", turned_down.name)),
	)

	print("\n--- sent back for correction, and the reason does not follow it forward ---")

	# Project autonames, so the record is carried rather than the label it was given.
	activity = frappe.get_doc({"doctype": "Project", "project_name": ACTIVITY}).insert(ignore_permissions=True)
	participants = as_user(
		REQUESTER,
		lambda: frappe.get_doc(
			{"doctype": "Activity Participant List", "activity": activity.name, "session_date": nowdate()}
		).insert(),
	)
	as_user(
		REQUESTER,
		lambda: apply_workflow(frappe.get_doc("Activity Participant List", participants.name), "Submit for Verification"),
	)

	RETURNED = "Two attendance signatures are missing."
	as_user(
		PROGRAMS_HEAD,
		lambda: (
			hold_rejection_reason("Activity Participant List", participants.name, RETURNED),
			apply_workflow(frappe.get_doc("Activity Participant List", participants.name), "Return for Correction"),
		),
	)
	sent_back = frappe.get_doc("Activity Participant List", participants.name)
	check(
		"a return for correction needs a reason in the same way a rejection does",
		sent_back.workflow_state == "Draft" and sent_back.get(REJECTION_REASON_FIELD) == RETURNED,
		f"{sent_back.workflow_state} / {sent_back.get(REJECTION_REASON_FIELD)!r}",
	)

	as_user(
		REQUESTER,
		lambda: apply_workflow(frappe.get_doc("Activity Participant List", participants.name), "Submit for Verification"),
	)
	resubmitted = frappe.get_doc("Activity Participant List", participants.name)
	check(
		"resubmitting clears it, so the next reader is not shown a stale complaint",
		not resubmitted.get(REJECTION_REASON_FIELD),
		repr(resubmitted.get(REJECTION_REASON_FIELD)),
	)
	check(
		"but the timeline still has it",
		any(RETURNED in content for content in workflow_comments("Activity Participant List", participants.name)),
		"; ".join(workflow_comments("Activity Participant List", participants.name)),
	)

	print("\n--- and the Desk is told which actions to ask about ---")

	turn_down_count = sum(
		1
		for name in frappe.get_all("Workflow", filters={"is_active": 1}, pluck="name")
		for wf in [frappe.get_doc("Workflow", name)]
		for t in wf.transitions
		if is_turn_down(wf, t.state, t.next_state)
	)
	boot = frappe._dict()
	frappe.get_attr("folt_customizations.workflow_access.add_turn_downs_to_bootinfo")(boot)
	in_boot = sum(len(spec["turn_downs"]) for spec in boot.folt_turn_downs.values())
	check(
		"every turn-down the workflows have is offered to the form script",
		in_boot == turn_down_count and turn_down_count > 0,
		f"{in_boot} of {turn_down_count} across {len(boot.folt_turn_downs)} doctypes",
	)

	print("\n--- FoLT's own code still saves what it needs to ---")

	def system_edit():
		doc = frappe.get_doc("Activity Requisition", requisition.name)
		doc.budget_amount = 51_000
		doc.save(ignore_permissions=True)

	as_user(REQUESTER, system_edit)
	check(
		"a save that says ignore_permissions is the system acting, and is let through",
		frappe.db.get_value("Activity Requisition", requisition.name, "budget_amount") == 51_000,
	)

	print(f"\n  {len(PASS)} passed, {len(FAIL)} failed")
	if FAIL:
		print("  FAILED: " + "; ".join(FAIL))
	return {"passed": len(PASS), "failed": len(FAIL), "failures": FAIL}
