"""End-to-end check of /folt's server half: spa.py and the step_forms registry behind it.

What this proves, in the order it would otherwise fail quietly:

  - the registry agrees with the live workflows and meta (step_forms.audit)
  - a step writes only what it lists: a read-only field, a field legal at a different state, a
    permlevel-1 field and a non-allow_on_submit field after submit are each REFUSED BY NAME
  - only the state's custodian edits it, and a value cannot ride on a transition whose actor is
    not the custodian (the Executive Director approving a float out of the Finance Officer's step)
  - a turn-down and its reason are ONE call, a blank reason is refused before anything is written,
    and the reason lands in the same timeline the Desk writes
  - "Save & Approve" saves the edit and then transitions, and the edit survives apply_workflow's
    reload -- the ordering bug that loses data silently if it is ever reversed
  - frappe's self-approval rule hides the action from the document's owner
  - child rows are patched by name: added, updated, removed on purpose, never deleted by omission
  - a colleague's committee score survives a member saving their own, and a member cannot write
    another member's row
  - pickers answer a permission gap with a reason rather than a 403
  - the three bundled fixes hold (Expense Claim reject, the owner's queue, the Done bucket)

THIS SUITE DELIBERATELY DOES NOT SET frappe.flags.in_test. workflow_access treats in_test as "the
system acting" and steps both the custodian check and the reason requirement aside, so a copy of
the Gosolar suite that set it would pass while testing nothing it claims to.

Every step is taken as the role that takes it in real life. Idempotent: tears its fixtures down
first and last. Run with:

    bench --site <site> execute folt_customizations.spa_e2e.run
"""

import frappe
from frappe.model.workflow import get_workflow
from frappe.utils import add_days, nowdate

from folt_customizations import spa, step_forms
from folt_customizations.workflow import roles_the_owner_can_move

ACTIVITY = "E2E SPA Borehole Committee Training"
REJECTED_ACTIVITY = "E2E SPA Turned Down Activity"
BUDGET_LINE = "4.1.2"

REQUESTER = "requester.test@folt.test"
HEAD_OF_PROGRAMS = "hop.test@folt.test"
HEAD_OF_FINANCE = "hof.test@folt.test"
FINANCE_OFFICER = "finofficer.test@folt.test"
EXECUTIVE_DIRECTOR = "ed.test@folt.test"
OPERATIONS = "oso.test@folt.test"
FINANCE_MANAGER = "finmanager.test@folt.test"
COMMITTEE = "committee.test@folt.test"
PURCHASER = "purchaser.test@folt.test"

USERS = (REQUESTER, HEAD_OF_PROGRAMS, HEAD_OF_FINANCE, FINANCE_OFFICER, EXECUTIVE_DIRECTOR, OPERATIONS, FINANCE_MANAGER)

PASS, FAIL = [], []


def check(label, condition, detail=""):
	(PASS if condition else FAIL).append(label)
	print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")


def refused(fn, exc=Exception) -> str | None:
	"""The refusal's text, or None if nothing was refused."""
	try:
		fn()
	except exc as e:  # noqa: BLE001
		# frappe.has_permission(throw=True) raises PermissionError with an EMPTY message, so the
		# type is the answer when the text is blank -- otherwise a real refusal reads as none.
		return str(e).replace("\n", " ")[:160] or type(e).__name__
	return None


def as_user(user, fn):
	previous = frappe.session.user
	frappe.set_user(user)
	frappe.local.message_log = []
	try:
		return fn()
	finally:
		frappe.set_user(previous)


def require_users():
	missing = [user for user in USERS if not frappe.db.exists("User", user)]
	if missing:
		raise SystemExit(f"seed the role test users first (seed_test_users.py): missing {missing}")


def teardown():
	frappe.set_user("Administrator")
	for doctype, filters in (
		("Participant Reimbursement List", {"activity": ACTIVITY}),
		("Activity Participant List", {"activity": ACTIVITY}),
		("Employee Advance", {"purpose": ["like", f"%{ACTIVITY}%"]}),
		("Activity Requisition", {"activity_program": ["in", [ACTIVITY, REJECTED_ACTIVITY]]}),
	):
		for name in frappe.get_all(doctype, filters={**filters, "docstatus": ["<", 2]}, pluck="name"):
			doc = frappe.get_doc(doctype, name)
			doc.flags.ignore_permissions = True
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
	for name in frappe.get_all("FoLT Participant", filters={"participant_name": ["like", "E2E SPA %"]}, pluck="name"):
		frappe.delete_doc("FoLT Participant", name, force=True, ignore_permissions=True)
	for project in (ACTIVITY, REJECTED_ACTIVITY):
		if frappe.db.exists("Project", project):
			frappe.delete_doc("Project", project, force=True, ignore_permissions=True)
	frappe.db.commit()


def requisition_values(activity):
	return {
		"activity_program": activity,
		"activity_date": nowdate(),
		"activity_end_date": add_days(nowdate(), 1),
		"venue": "E2E SPA Lodwar Hall",
		"budget_amount": 80000,
		"budget_line": BUDGET_LINE,
		"float_required": 1,
		"float_amount": 60000,
		"description": "E2E: training for borehole committees",
	}


def state_of(doctype, name):
	return frappe.db.get_value(doctype, name, "workflow_state")


def run():
	frappe.set_user("Administrator")
	frappe.flags.mute_emails = True
	assert not frappe.flags.in_test, "spa_e2e must not run with in_test set -- see the module docstring"
	require_users()
	teardown()

	try:
		_run()
	finally:
		frappe.db.rollback()
		teardown()

	print(f"\nRESULT: {len(PASS)} passed, {len(FAIL)} failed")
	if FAIL:
		for label in FAIL:
			print(f"  FAILED: {label}")
		raise SystemExit(1)


def _run():
	print("\n--- 1  the registry agrees with the site ---")
	problems = step_forms.audit()
	check("step_forms.audit() finds nothing wrong", not problems, "; ".join(problems[:3]))

	print("\n--- 2  reading, as every role, without permission dialogs ---")
	for user in (REQUESTER, HEAD_OF_PROGRAMS, HEAD_OF_FINANCE, FINANCE_OFFICER, EXECUTIVE_DIRECTOR, OPERATIONS, FINANCE_MANAGER):
		def read():
			catalogue = spa.catalogue()
			for wf in catalogue[:3]:
				spa.documents(wf["doctype"], limit=5)
			return catalogue, list(frappe.local.message_log)
		catalogue, log = as_user(user, read)
		check(f"{user.split('.')[0]}: catalogue and lists raise no message dialogs", not log, str(log)[:120])
	creatable = as_user(REQUESTER, lambda: {w["doctype"] for w in spa.catalogue() if w["can_create"]})
	check("the requester may start an activity requisition here", "Activity Requisition" in creatable, str(creatable))
	creatable = as_user(OPERATIONS, lambda: {w["doctype"] for w in spa.catalogue() if w["can_create"]})
	check("the operations officer may start a waiver request here", "Derogation Waiver Request" in creatable, str(creatable))
	creatable = as_user(FINANCE_MANAGER, lambda: {w["doctype"] for w in spa.catalogue() if w["can_create"]})
	check("the finance manager starts nothing (orders come from hand-offs)", not creatable, str(creatable))

	print("\n--- 3  starting a chain ---")
	created = as_user(REQUESTER, lambda: spa.create("Activity Requisition", requisition_values(ACTIVITY)))
	name = created["name"]
	doc = frappe.get_doc("Activity Requisition", name)
	check("a requisition is raised from a patch, at Draft", doc.workflow_state == "Draft", f"{name} / {doc.workflow_state}")
	check("the controller filled what the form does not offer", bool(doc.company and doc.requested_by), f"{doc.company} / {doc.requested_by}")
	check(
		"the new document's own screen comes back with it",
		created["document"]["name"] == name and created["document"]["form"]["editable"],
	)
	why = refused(lambda: as_user(HEAD_OF_FINANCE, lambda: spa.create("Activity Requisition", requisition_values(ACTIVITY))))
	check("a role without create is refused", bool(why), why or "")
	why = refused(lambda: as_user(REQUESTER, lambda: spa.create("Purchase Order", {})))
	check("an order is not raised from nothing -- it comes from a hand-off", bool(why), why or "")

	print("\n--- 4  a step writes only what it lists ---")
	modified = str(doc.modified)
	why = refused(lambda: as_user(REQUESTER, lambda: spa.save("Activity Requisition", name, {"folt_rejection_reason": "x"}, modified)))
	check("a read-only field is refused, by name", bool(why) and "Rejection Reason" in why, why or "")
	why = refused(lambda: as_user(REQUESTER, lambda: spa.save("Activity Requisition", name, {"project": ACTIVITY}, modified)))
	check("a field that belongs to a later step is refused at this one", bool(why) and "Project" in why, why or "")
	why = refused(lambda: as_user(REQUESTER, lambda: spa.save("Activity Requisition", name, {"venue": "Elsewhere"}, "2000-01-01 00:00:00")))
	check("a save against a stale version is refused", bool(why) and "changed since" in why, why or "")
	saved = as_user(REQUESTER, lambda: spa.save("Activity Requisition", name, {"venue": "E2E SPA Kakuma Centre"}, modified))
	check("a listed field saves", frappe.db.get_value("Activity Requisition", name, "venue") == "E2E SPA Kakuma Centre")
	check("the save hands back the new version", saved["modified"] != modified, saved["modified"])

	print("\n--- 5  acting ---")
	actions = as_user(REQUESTER, lambda: spa.document("Activity Requisition", name)["actions"])
	check(
		"the requester is offered Submit for Review, as the primary forward action",
		[a["action"] for a in actions] == ["Submit for Review"] and actions[0]["primary"],
		str([(a["action"], a["kind"]) for a in actions]),
	)
	as_user(REQUESTER, lambda: spa.act("Activity Requisition", name, "Submit for Review", modified=saved["modified"]))
	check("Submit for Review moves it on", state_of("Activity Requisition", name) == "Pending Head of Programs")
	current = str(frappe.db.get_value("Activity Requisition", name, "modified"))
	why = refused(lambda: as_user(REQUESTER, lambda: spa.save("Activity Requisition", name, {"venue": "Z"}, current)))
	check("the requester can no longer edit it -- nothing is edited at this step", bool(why), why or "")
	why = refused(
		lambda: as_user(HEAD_OF_PROGRAMS, lambda: spa.act("Activity Requisition", name, "Approve", values={"budget_line": "1.1"}, modified=current))
	)
	check("values cannot ride on a transition out of an act-only step", bool(why), why or "")
	check("...and the refusal left it where it was", state_of("Activity Requisition", name) == "Pending Head of Programs")

	print("\n--- 6  turning down: one call, reason first ---")
	other = as_user(REQUESTER, lambda: spa.create("Activity Requisition", requisition_values(REJECTED_ACTIVITY)))["name"]
	as_user(REQUESTER, lambda: spa.act("Activity Requisition", other, "Submit for Review"))
	before = str(frappe.db.get_value("Activity Requisition", other, "modified"))
	why = refused(lambda: as_user(HEAD_OF_PROGRAMS, lambda: spa.act("Activity Requisition", other, "Reject", reason="   ")))
	check("a turn-down with no reason is refused", bool(why) and "reason" in why.lower(), why or "")
	check(
		"...before anything was written",
		state_of("Activity Requisition", other) == "Pending Head of Programs"
		and str(frappe.db.get_value("Activity Requisition", other, "modified")) == before,
	)
	reason = "The venue is double-booked; move it to the week after."
	after = as_user(HEAD_OF_PROGRAMS, lambda: spa.act("Activity Requisition", other, "Reject", reason=reason, modified=before))
	check("reason and turn-down travel in one call", state_of("Activity Requisition", other) == "Rejected")
	check("the reason is on the document", frappe.db.get_value("Activity Requisition", other, "folt_rejection_reason") == reason)
	turned = [e for e in after["guide"]["timeline"] if e["kind"] == "turned_down"]
	check(
		"...and in the same timeline the Desk writes, once",
		len(turned) == 1 and turned[0]["reason"] == reason,
		str([(e["kind"], e["reason"]) for e in after["guide"]["timeline"]]),
	)

	print("\n--- 7  Save & Approve: the custodian's edit rides on the transition ---")
	as_user(HEAD_OF_PROGRAMS, lambda: spa.act("Activity Requisition", name, "Approve"))
	check("the Head of Programs approves it on", state_of("Activity Requisition", name) == "Pending Head of Finance")
	approved = as_user(
		HEAD_OF_FINANCE, lambda: spa.act("Activity Requisition", name, "Approve", values={"budget_line": "9.9.9"})
	)
	doc = frappe.get_doc("Activity Requisition", name)
	check("the Head of Finance's Save & Approve submits it", doc.workflow_state == "Approved" and doc.docstatus == 1)
	check("the edit survived apply_workflow's reload from the database", doc.budget_line == "9.9.9", doc.budget_line)
	check("approval opened the activity's Project", bool(doc.project), str(doc.project))
	check(
		"the approved requisition offers its two hand-offs, both ready",
		sorted(h["label"] for h in approved["guide"]["handoffs"] if h["ready"]) == ["Attendance Register", "Float Request"],
		str([(h["label"], h["ready"]) for h in approved["guide"]["handoffs"]]),
	)

	print("\n--- 8  hand-offs ---")
	why = refused(lambda: as_user(REQUESTER, lambda: spa.handoff("Activity Requisition", name, "Purchase Order")))
	check("a hand-off the chain does not declare is refused", bool(why), why or "")
	made = as_user(REQUESTER, lambda: spa.handoff("Activity Requisition", name, "Employee Advance"))
	advance = made["name"]
	check("the float request is raised from the requisition", made["doctype"] == "Employee Advance" and bool(advance), str(made)[:120])
	register = as_user(REQUESTER, lambda: spa.handoff("Activity Requisition", name, "Activity Participant List"))["name"]
	check("so is the attendance register", state_of("Activity Participant List", register) == "Draft", register)

	print("\n--- 9  who may edit, on a chain where the approver is not the custodian ---")
	current = str(frappe.db.get_value("Employee Advance", advance, "modified"))
	why = refused(lambda: as_user(FINANCE_OFFICER, lambda: spa.act("Employee Advance", advance, "Check", values={"purpose": "changed"}, modified=current)))
	check("the Finance Officer cannot edit a Requested float on the way through (Employee holds it)", bool(why), why or "")
	as_user(FINANCE_OFFICER, lambda: spa.act("Employee Advance", advance, "Check"))
	check("...but can Check it", state_of("Employee Advance", advance) == "Checked")
	saved = as_user(FINANCE_OFFICER, lambda: spa.save("Employee Advance", advance, {"folt_budget_line": "7.7.7"}))
	check("at Checked the Finance Officer is the custodian and may correct it", frappe.db.get_value("Employee Advance", advance, "folt_budget_line") == "7.7.7")
	why = refused(
		lambda: as_user(EXECUTIVE_DIRECTOR, lambda: spa.act("Employee Advance", advance, "Approve", values={"purpose": "x"}, modified=saved["modified"]))
	)
	check("the Executive Director's approval cannot carry an edit to the Finance Officer's step", bool(why), why or "")
	check("...and the float is still at Checked", state_of("Employee Advance", advance) == "Checked")

	print("\n--- 10  self-approval ---")
	owner = frappe.db.get_value("Employee Advance", advance, "owner")
	frappe.db.set_value("Employee Advance", advance, "owner", EXECUTIVE_DIRECTOR, update_modified=False)
	offered = as_user(EXECUTIVE_DIRECTOR, lambda: [a["action"] for a in spa.document("Employee Advance", advance)["actions"]])
	check("Approve (allow_self_approval 0) is not offered to the float's owner", "Approve" not in offered, str(offered))
	why = refused(lambda: as_user(EXECUTIVE_DIRECTOR, lambda: spa.act("Employee Advance", advance, "Approve")))
	check("...and refused if asked for anyway", bool(why), why or "")
	frappe.db.set_value("Employee Advance", advance, "owner", owner, update_modified=False)
	offered = as_user(EXECUTIVE_DIRECTOR, lambda: [a["action"] for a in spa.document("Employee Advance", advance)["actions"]])
	check("...while somebody else's float offers it", "Approve" in offered, str(offered))

	print("\n--- 11  the write-time guards the registry audit also checks ---")
	fake = step_forms.step(fields=("folt_project",))
	submitted = frappe.get_doc("Activity Requisition", name)
	why = refused(lambda: spa._apply_patch(submitted, step_forms.step(fields=("venue",)), {"venue": "x"}))
	check("a non-allow_on_submit field is refused on a submitted document", bool(why) and "submitted" in why, why or "")
	claim_meta = frappe.get_meta("Expense Claim")
	check("approval_status is permlevel 1 on this site", int(claim_meta.get_field("approval_status").permlevel or 0) == 1)
	claim = frappe.new_doc("Expense Claim")
	why = refused(lambda: spa._apply_patch(claim, step_forms.step(fields=("approval_status",)), {"approval_status": "Approved"}))
	check("a permlevel-1 field is refused even if a registry entry listed it", bool(why), why or "")
	del fake

	print("\n--- 12  child rows, by name ---")
	current = str(frappe.db.get_value("Activity Participant List", register, "modified"))
	rows = [
		{"participant_name": "E2E SPA Akai", "mobile_number": "0712800001", "location": "Kanamkemer", "category": "Community Participant", "attended": 1},
		{"participant_name": "E2E SPA Ekal", "mobile_number": "0712800002", "location": "Nakalale", "category": "Community Participant", "attended": 1},
		{"participant_name": "E2E SPA Lokol", "mobile_number": "0712800003", "location": "Nakalale", "category": "Community Participant", "attended": 0},
	]
	as_user(REQUESTER, lambda: spa.save("Activity Participant List", register, {"participants": {"add": rows}}, current))
	saved_rows = frappe.get_doc("Activity Participant List", register).participants
	check("three attendees added", len(saved_rows) == 3, str(len(saved_rows)))
	check(
		"a row added here is unacknowledged, not silently 'Signature'",
		all(r.acknowledgement in ("", None, "None") for r in saved_rows),
		str([r.acknowledgement for r in saved_rows]),
	)
	first, second, third = saved_rows
	as_user(
		REQUESTER,
		lambda: spa.save(
			"Activity Participant List",
			register,
			{"participants": {"update": [{"name": first.name, "acknowledgement": "Signature"}], "remove": [third.name]}},
		),
	)
	after_rows = {r.name: r for r in frappe.get_doc("Activity Participant List", register).participants}
	check("an update by name changes that row", after_rows[first.name].acknowledgement == "Signature")
	check("a row the patch does not mention is left alone", second.name in after_rows)
	check("a row removed on purpose is gone", third.name not in after_rows)
	why = refused(
		lambda: as_user(REQUESTER, lambda: spa.save("Activity Participant List", register, {"participants": {"update": [{"name": "nope-1", "attended": 1}]}}))
	)
	check("a row that is not on the document is refused", bool(why), why or "")
	why = refused(
		lambda: as_user(
			REQUESTER, lambda: spa.save("Activity Participant List", register, {"participants": {"update": [{"name": first.name, "payment_status": "Paid"}]}})
		)
	)
	check("a column the step does not list is refused", bool(why), why or "")

	print("\n--- 13  attachments are gated before anything is stored ---")
	why = refused(lambda: as_user(HEAD_OF_FINANCE, lambda: spa.attach("Activity Participant List", register, "attendance_sheet")))
	check("somebody who does not hold the step cannot attach to it", bool(why), why or "")
	why = refused(lambda: as_user(REQUESTER, lambda: spa.attach("Activity Participant List", register, "activity")))
	check("only an Attach field the step lists takes a file", bool(why) and "not an attachment" in why, why or "")
	why = refused(lambda: as_user(REQUESTER, lambda: spa.attach("Activity Participant List", register, "attendance_sheet")))
	check("the custodian passes every gate (and is told no file came)", bool(why) and "No file" in why, why or "")

	print("\n--- 14  pickers ---")
	answer = as_user(HEAD_OF_FINANCE, lambda: spa.options("Activity Requisition", "donor"))
	check(
		"a role that cannot list Donors gets a reason, not a 403",
		answer["allowed"] is False and bool(answer["reason"]) and not frappe.local.message_log,
		str(answer)[:120],
	)
	answer = as_user(REQUESTER, lambda: spa.options("Activity Participant List", "participants.location", "Nak"))
	check("a child column's picker answers with labelled options", answer["allowed"] and any("Nakalale" in o["label"] for o in answer["options"]), str(answer)[:120])
	why = refused(lambda: as_user(REQUESTER, lambda: spa.options("Activity Requisition", "company")))
	check("a field no step form offers has no picker", bool(why), why or "")

	print("\n--- 15  the committee's own rows ---")
	_committee_rows()

	print("\n--- 16  the fixes bundled with this ---")
	retirement = get_workflow("Expense Claim")
	rejected = next(s for s in retirement.states if s.state == "Rejected")
	check(
		"Expense Claim's Rejected state no longer zeroes a claim (no update_field)",
		not rejected.update_field,
		str(rejected.update_field),
	)
	order = get_workflow("Purchase Order")
	check(
		"a PO's owner cannot move it out of Pending Approval, so it is nobody's own to-do",
		roles_the_owner_can_move(order, "Pending Approval") == [],
		str(roles_the_owner_can_move(order, "Pending Approval")),
	)
	check(
		"a requisition's author can submit their own draft",
		roles_the_owner_can_move(get_workflow("Activity Requisition"), "Draft") == ["Employee"],
	)
	float_flow = get_workflow("Employee Advance")
	submitted_states = {s.state for s in float_flow.states if int(s.doc_status or 0) == 1}
	check("Overdue is a submitted state the Done bucket now reads", "Overdue" in submitted_states)


def _committee_rows():
	"""On a real evaluation in Committee Reviewing, inside a savepoint that is rolled back."""
	evaluation = next(
		(
			name
			for name in frappe.get_all("Procurement Committee Evaluation", filters={"workflow_state": "Committee Reviewing"}, pluck="name")
			if len({m.member for m in frappe.get_doc("Procurement Committee Evaluation", name).members}) >= 2
			and COMMITTEE in {m.member for m in frappe.get_doc("Procurement Committee Evaluation", name).members}
		),
		None,
	)
	if not evaluation:
		print("  note  not exercised -- no evaluation in Committee Reviewing has the committee test user and a colleague")
		return

	frappe.db.savepoint("spa_committee")
	try:
		doc = frappe.get_doc("Procurement Committee Evaluation", evaluation)
		mine = next(r for r in doc.quotation_scores if r.member == COMMITTEE)
		theirs = next(r for r in doc.quotation_scores if r.member != COMMITTEE)
		their_score = theirs.score
		as_user(COMMITTEE, lambda: spa.save(doc.doctype, doc.name, {"quotation_scores": {"update": [{"name": mine.name, "score": 7.5}]}}))
		reloaded = {r.name: r for r in frappe.get_doc(doc.doctype, doc.name).quotation_scores}
		check("a member's own score saves", float(reloaded[mine.name].score or 0) == 7.5)
		check("a colleague's score is untouched by it", reloaded[theirs.name].score == their_score)
		why = refused(
			lambda: as_user(COMMITTEE, lambda: spa.save(doc.doctype, doc.name, {"quotation_scores": {"update": [{"name": theirs.name, "score": 1}]}}))
		)
		check("a member cannot write a colleague's row", bool(why) and "belongs to" in why, why or "")
	finally:
		frappe.db.rollback(save_point="spa_committee")
