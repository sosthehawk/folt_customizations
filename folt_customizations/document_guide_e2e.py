"""End-to-end check that the guide on a document says true things about it.

Three claims, and the interesting thing about all three is that being wrong is silent. A tracker
that misreports a step still renders; a checklist that has stopped predicting a block still
shows a tidy green row; a timeline missing an entry looks like a document that simply had a
quiet week. None of it throws, so none of it gets reported.

  THE CHECKLIST IS PREDICTIVE, AND HONEST ABOUT IT. `blocked_by` claims a named attachment is
  going to stop the very next step. That is only worth putting on a form if it is true in both
  directions -- present when the gate is real and absent when it is not -- and if the gate it
  names still exists. So for every DOCUMENTS entry that names an enforcer, the enforcer is made
  to throw here. An entry that has quietly stopped being enforced fails this driver instead of
  misinforming somebody.

  THE TIMELINE IS COMPLETE AND NOT DOUBLED. A turn-down writes two Comment rows, milliseconds
  apart, one carrying the reason and one not. Collapsing them is presentation, and the test is
  that the reason survives the collapse -- because the reason is the entire reason the entry
  matters to whoever has to act on it next.

  THE GUIDE IS NOT A WAY ROUND PERMISSIONS. get_guide reports who may act and what is missing,
  on a document the caller has to be allowed to read.

Idempotent: tears down its own fixtures first. Run with

    bench --site <site> execute folt_customizations.document_guide_e2e.run
"""

import frappe
from frappe.apps import get_default_path
from frappe.model.workflow import apply_workflow
from frappe.utils import add_days, nowdate

from folt_customizations import activity_chain, document_guide
from folt_customizations.document_guide import DOCUMENTS, get_guide
from folt_customizations.folt_customizations.page.folt_tasks.folt_tasks import my_tasks
from folt_customizations.workflow_access import hold_rejection_reason
from folt_customizations.workspaces import set_landing_page

ACTIVITY = "E2E Guide Water Point Rehabilitation"
BUDGET = 120000
BUDGET_LINE = "3.1.1"
VENUE = "E2E Guide Community Hall"

REQUESTER = "requester.test@folt.test"
HEAD_OF_PROGRAMS = "hop.test@folt.test"
HEAD_OF_FINANCE = "hof.test@folt.test"
# Holds none of the roles in the requisition or register chains, so it is the right user to ask
# "does the guide refuse a document this person cannot read".
OUTSIDER = "purchaser.test@folt.test"

USERS = (REQUESTER, HEAD_OF_PROGRAMS, HEAD_OF_FINANCE, OUTSIDER)

ATTENDEES = [
	{
		"participant_name": "E2E Guide Kalokol",
		"mobile_number": "0712910001",
		"location": "Kalokol",
		"category": "Community Participant",
	},
	{
		"participant_name": "E2E Guide Lodwar",
		"mobile_number": "0712910002",
		"location": "Lodwar",
		"category": "Community Participant",
	},
]

RETURNED_REASON = "The second signature is illegible."

# The desk Page this app ships, and where staff are meant to land.
TASKS_PAGE = "folt-tasks"

PASS, FAIL = [], []


def check(label, condition, detail=""):
	(PASS if condition else FAIL).append(label)
	print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")


def expect_throw(label, fn, exc=frappe.ValidationError):
	"""As in the other drivers, but with the exception type named.

	frappe.PermissionError does not descend from frappe.ValidationError, so a permission check
	asserted with the default would pass for the wrong reason -- or rather, fail as "wrong error
	type" while the code was in fact correct.
	"""
	try:
		fn()
	except exc as e:
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


def as_fresh_user(user, fn):
	"""as_user, with the per-request memoisation switched off for the duration.

	Needed for anything that goes through `frappe.apps.get_apps()`, which is decorated with
	`@request_cache` and takes no arguments -- so its answer is memoised per *request*, not per
	user. A `bench execute` is one long request, so impersonating five users in a row and asking
	each where they land returns the first user's answer five times. That reads as a pass when
	the roles happen to agree and as a spurious failure when they should differ, which is exactly
	what it did here: portal users appeared to be landing in the Desk because a staff user had
	already warmed the cache.

	Set to None rather than to {}: the decorator treats None as "no cache, call through", whereas
	an empty dict is a broken cache -- on a miss it does `_cache[func][args_key] = ...`, which
	needs the defaultdict(dict) frappe installs, and a plain {} raises KeyError instead.
	"""
	previous = getattr(frappe.local, "request_cache", None)
	frappe.local.request_cache = None
	try:
		return as_user(user, fn)
	finally:
		frappe.local.request_cache = previous


# --- fixtures --------------------------------------------------------------------------------


def require_users():
	missing = [user for user in USERS if not frappe.db.exists("User", user)]
	if missing:
		raise SystemExit(f"seed the role test users first (seed_test_users.py): missing {missing}")


def teardown():
	# The Project a requisition opens is autonamed (PROJ-0006), not named after the programme, so
	# the registers hanging off it are found via the Project rather than by the programme name --
	# the workflow_access_e2e pattern. Filtering registers on `activity` directly would silently
	# match nothing and leave the previous run's documents behind, which then blocks the
	# requisition delete with a LinkExistsError.
	activities = frappe.get_all("Project", filters={"project_name": ACTIVITY}, pluck="name")

	# Deleted innermost first: a register links to its requisition, and a requisition to its
	# project, so any other order trips over a link that still exists.
	for doctype, filters in (
		("Activity Participant List", {"activity": ("in", activities or [""])}),
		("Activity Requisition", {"activity_program": ACTIVITY}),
	):
		for name in frappe.get_all(doctype, filters={**filters, "docstatus": ["<", 2]}, pluck="name"):
			doc = frappe.get_doc(doctype, name)
			if doc.docstatus == 1:
				doc.cancel()
			doc.delete(force=True)

	for name in frappe.get_all(
		"FoLT Participant", filters={"participant_name": ["like", "E2E Guide %"]}, pluck="name"
	):
		frappe.delete_doc("FoLT Participant", name, force=True, ignore_permissions=True)

	for name in activities:
		frappe.delete_doc("Project", name, force=True, ignore_permissions=True)

	frappe.db.commit()


def employee_for(user):
	return frappe.db.get_value("Employee", {"user_id": user, "status": "Active"})


def make_requisition():
	return frappe.get_doc(
		{
			"doctype": "Activity Requisition",
			"activity_program": ACTIVITY,
			"requested_by": employee_for(REQUESTER),
			"activity_date": nowdate(),
			"activity_end_date": add_days(nowdate(), 1),
			"venue": VENUE,
			"company": frappe.defaults.get_defaults().get("company")
			or frappe.get_all("Company", pluck="name")[0],
			"budget_amount": BUDGET,
			"budget_line": BUDGET_LINE,
			"float_required": 0,
			"description": "E2E guide: attendance register for a water point rehabilitation",
		}
	).insert()


def approve_requisition(requisition):
	as_user(REQUESTER, lambda: apply_workflow(requisition, "Submit for Review"))
	as_user(HEAD_OF_PROGRAMS, lambda: apply_workflow(requisition, "Approve"))
	as_user(HEAD_OF_FINANCE, lambda: apply_workflow(requisition, "Approve"))
	requisition.reload()
	return requisition


def states_of(guide):
	return [(entry["kind"], entry["state"], entry["reason"]) for entry in guide["timeline"]]


# --- checks ----------------------------------------------------------------------------------


def run():
	frappe.set_user("Administrator")
	frappe.flags.mute_emails = True
	require_users()
	teardown()

	print("\n--- the registry describes fields and states that exist ---")

	problems = document_guide.audit(verbose=False)["problems"]
	check("every DOCUMENTS entry names a real attachment and real states", not problems, "; ".join(problems))

	# The Custom Field fixtures are part of the same contract, and they carry a failure mode the
	# Desk hides. `folt_rejection_reason` shipped with mandatory_depends_on
	# "eval:!doc.folt_waiver_request" on four doctypes -- copy-pasted from
	# Purchase Order.folt_supplier_group, where it is correct -- and only Purchase Order has a
	# folt_waiver_request field. On the other three the expression names nothing, is therefore
	# always truthy, and makes a read_only field mandatory. Invisible in the Desk because the field
	# is also depends_on-hidden, and fatal to any client that evaluates the expression honestly.
	#
	# So this asserts the class, not the instance: every doc.<field> reference in every conditional
	# expression in the fixture must name a field that doctype actually has.
	import json
	import re

	with open(frappe.get_app_path("folt_customizations", "fixtures", "custom_field.json")) as fh:
		custom_fields = json.load(fh)

	dangling = []
	for row in custom_fields:
		if not frappe.db.exists("DocType", row["dt"]):
			continue
		meta = frappe.get_meta(row["dt"])
		for prop in ("depends_on", "mandatory_depends_on", "read_only_depends_on"):
			for referenced in re.findall(r"doc\.(\w+)", row.get(prop) or ""):
				if not meta.has_field(referenced):
					dangling.append(f"{row['dt']}.{row['fieldname']}.{prop} -> doc.{referenced}")

	check(
		"no conditional expression on a custom field names a field its doctype lacks",
		not dangling,
		"; ".join(dangling) if dangling else f"{len(custom_fields)} custom fields checked",
	)

	# The dangling check above catches three of the four rows the bug shipped on, and misses
	# Purchase Order -- because folt_waiver_request genuinely exists there, so the expression was
	# evaluable and merely wrong. This is the invariant that covers all four: a field nobody can
	# type into must not be required. FoLT populates folt_rejection_reason from
	# workflow_access.require_rejection_reason, so demanding it from the person filling the form is
	# asking for something the form does not let them give.
	contradictory = [
		f"{row['dt']}.{row['fieldname']}"
		for row in custom_fields
		if row.get("read_only") and (row.get("mandatory_depends_on") or row.get("reqd"))
	]
	check(
		"no read-only custom field is also required",
		not contradictory,
		"; ".join(contradictory) if contradictory else "none",
	)

	print("\n--- a register that has not got its evidence yet ---")

	requisition = approve_requisition(as_user(REQUESTER, make_requisition))
	register_name = as_user(REQUESTER, lambda: activity_chain.make_attendance_register(requisition.name))

	def fill_rows():
		doc = frappe.get_doc("Activity Participant List", register_name)
		for row in ATTENDEES:
			doc.append("participants", row)
		doc.save()
		return doc

	register = as_user(REQUESTER, fill_rows)

	guide = as_user(REQUESTER, lambda: get_guide("Activity Participant List", register_name))
	check(
		"the register is on step 1 of 3, and knows it is step 4 of the SOP",
		(guide["lane"], guide["of"]) == (0, 3) and guide["chain"]["step"] == 4,
		f"step {guide['lane'] + 1} of {guide['of']}, SOP step {guide['chain']['step']} of {guide['chain']['of']}",
	)

	sheet = next(row for row in guide["documents"] if row["fieldname"] == "attendance_sheet")
	check(
		"the missing attendance sheet is named, with the form's own words for it",
		not sheet["attached"] and sheet["label"] == "Signed attendance sheet" and sheet["description"],
		f"{sheet['label']}: {(sheet['description'] or '')[:50]}",
	)

	# In Draft the sheet is genuinely not needed yet: nothing the document can do next requires
	# it. Saying otherwise would make the checklist cry wolf on every new register.
	check(
		"but it does not block anything from Draft, because nothing next needs it",
		not sheet["blocks_next"] and guide["blocked_by"] == [],
		f"blocks: {sheet['blocks']}",
	)

	as_user(REQUESTER, lambda: apply_workflow(register, "Submit for Verification"))
	register.reload()

	guide = as_user(HEAD_OF_PROGRAMS, lambda: get_guide("Activity Participant List", register_name))
	check(
		"once it is up for verification, the same sheet is announced as blocking",
		guide["blocked_by"] == ["Signed attendance sheet"],
		f"blocked_by: {guide['blocked_by']}",
	)
	check(
		"and the block names the state it is going to bite at",
		next(
			row["blocks"] for row in guide["documents"] if row["fieldname"] == "attendance_sheet"
		) == ["Verified"],
	)

	print("\n--- and the block it predicted is real ---")

	# The claim the checklist is making, made to fail. DOCUMENTS says attendance_sheet is
	# enforced at Verified by before_submit; this is that enforcement.
	expect_throw(
		"verifying without the sheet is refused, exactly as the checklist said it would be",
		lambda: as_user(HEAD_OF_PROGRAMS, lambda: apply_workflow(register, "Verify")),
	)

	print("\n--- who has it, and whether that is me ---")

	check(
		"the register is waiting for the Head of Programs, and names who that is",
		guide["waiting_for"]["roles"] == ["Head of Programs"]
		and guide["waiting_for"]["approvers"]
		and not guide["waiting_for"]["unassigned"],
		f"{[a['full_name'] for a in guide['waiting_for']['approvers']]}",
	)
	check("and the Head of Programs is told they can act", guide["can_act"])

	requester_view = as_user(REQUESTER, lambda: get_guide("Activity Participant List", register_name))
	check(
		"while the requester is told it is not their move",
		not requester_view["can_act"],
		f"can_act={requester_view['can_act']}",
	)

	print("\n--- sending it back, and what the timeline keeps of that ---")

	# The reason is parked rather than set on the document, because apply_workflow does
	# load_from_db() and discards anything the caller had in memory -- which is the whole reason
	# hold_rejection_reason exists. Same shape as workflow_access_e2e.
	as_user(
		HEAD_OF_PROGRAMS,
		lambda: (
			hold_rejection_reason("Activity Participant List", register_name, RETURNED_REASON),
			apply_workflow(
				frappe.get_doc("Activity Participant List", register_name), "Return for Correction"
			),
		),
	)

	guide = as_user(REQUESTER, lambda: get_guide("Activity Participant List", register_name))
	returned = [entry for entry in guide["timeline"] if entry["kind"] == "turned_down"]
	check(
		"the return shows as a turn-down and not as progress",
		len(returned) == 1 and returned[0]["state"] == "Draft",
		"; ".join(f"{k}:{s}" for k, s, _r in states_of(guide)),
	)
	check(
		"it carries the reason that was given for it",
		returned and returned[0]["reason"] == RETURNED_REASON,
		f"{returned[0]['reason'] if returned else None}",
	)
	check(
		"and the two Comment rows a turn-down writes are one entry, not two",
		len([entry for entry in guide["timeline"] if entry["state"] == "Draft"]) == 2,
		# Two Draft entries are correct: the document was raised in Draft and later returned to
		# it. Three would mean the collapse had failed.
		"; ".join(f"{k}:{s}" for k, s, _r in states_of(guide)),
	)
	check(
		"the timeline starts with the document being raised, by whoever raised it",
		guide["timeline"][0]["kind"] == "raised"
		and guide["timeline"][0]["state"] == "Draft",
		f"{guide['timeline'][0]['kind']} / {guide['timeline'][0]['by_name']}",
	)

	print("\n--- the evidence arrives ---")

	def attach_and_verify():
		doc = frappe.get_doc("Activity Participant List", register_name)
		doc.attendance_sheet = "/files/e2e-guide-attendance.pdf"
		doc.save()
		apply_workflow(doc, "Submit for Verification")
		return doc

	register = as_user(REQUESTER, attach_and_verify)

	guide = as_user(HEAD_OF_PROGRAMS, lambda: get_guide("Activity Participant List", register_name))
	check(
		"with the sheet attached, nothing is blocking any more",
		guide["blocked_by"] == []
		and next(r for r in guide["documents"] if r["fieldname"] == "attendance_sheet")["attached"],
	)

	as_user(HEAD_OF_PROGRAMS, lambda: apply_workflow(register, "Verify"))
	register.reload()

	guide = as_user(HEAD_OF_PROGRAMS, lambda: get_guide("Activity Participant List", register_name))
	check(
		"a verified register is on the last step, with nothing waiting on anybody",
		guide["lane"] == guide["of"] - 1
		and guide["steps"][-1]["terminal"]
		and not guide["waiting_for"]["roles"],
		f"step {guide['lane'] + 1} of {guide['of']}, waiting for {guide['waiting_for']['roles']}",
	)
	check(
		"and what comes next in the SOP is offered from it",
		any(handoff["ready"] for handoff in guide["handoffs"]),
		"; ".join(f"{h['label']}:{'ready' if h['ready'] else h['ready_at']}" for h in guide["handoffs"]),
	)

	print("\n--- the other registry entry, and the rule it points at ---")

	# The reimbursement list's gate is asserted here rather than by building a float chain for it
	# -- activity_chain_e2e already drives that end to end. What is checked is the claim this
	# registry makes: that signed_list is enforced, at Paid, by validate.
	entry = next(row for row in DOCUMENTS["Participant Reimbursement List"] if row.fieldname == "signed_list")
	check(
		"the registry says the signed list is required at Paid, enforced in validate",
		entry.required_at == ("Paid",) and entry.enforced_by == "validate",
		f"required_at={entry.required_at} enforced_by={entry.enforced_by}",
	)

	def paid_with_no_signed_list():
		doc = frappe.get_doc(
			{
				"doctype": "Participant Reimbursement List",
				"workflow_state": "Paid",
				"signed_list": None,
			}
		)
		doc.validate_payout_evidence()

	expect_throw(
		"and marking a list paid with no signed list is refused by that method",
		paid_with_no_signed_list,
	)

	print("\n--- the guide is not a way round permissions ---")

	expect_throw(
		"a user who cannot read the register cannot get its guide either",
		lambda: as_user(OUTSIDER, lambda: get_guide("Activity Participant List", register_name)),
		exc=frappe.PermissionError,
	)

	print("\n--- doctypes with no workflow, and the Desk's copy ---")

	check(
		"a doctype with no workflow gets an empty guide rather than an error",
		as_user(REQUESTER, lambda: get_guide("User", REQUESTER)) == {},
	)

	active = frappe.get_all("Workflow", filters={"is_active": 1}, pluck="name")
	boot = frappe._dict()
	document_guide.add_guide_to_bootinfo(boot)
	check(
		"every active workflow is described to the Desk at boot",
		len(boot.folt_guide) == len(active) and len(active) > 0,
		f"{len(boot.folt_guide)} of {len(active)}",
	)
	check(
		"and each description carries the steps the form script draws",
		all(spec["lanes"] and spec["state_field"] for spec in boot.folt_guide.values()),
	)
	# The chain doctypes are the ones that also get "step N of 6", and the boot payload has to
	# carry that or folt_guide.js has nothing to put in the outer rail.
	check(
		"the SOP chain doctypes are marked as such in the boot payload",
		sum(1 for spec in boot.folt_guide.values() if spec["chain"]) == 5,
		f"{sum(1 for spec in boot.folt_guide.values() if spec['chain'])} chain doctypes",
	)

	print("\n--- and it all turns up in somebody's queue ---")

	# A second requisition, left unsubmitted, so there is something that belongs in Drafts and
	# must not appear in an approval queue.
	draft = as_user(REQUESTER, make_requisition)

	def names_in(result):
		return {row["name"] for group in result["groups"] for row in group["rows"]}

	hop_queue = as_user(HEAD_OF_PROGRAMS, lambda: my_tasks("awaiting"))
	check(
		"the verified register has left the Head of Programs' queue",
		register_name not in names_in(hop_queue),
		f"{len(names_in(hop_queue))} items awaiting",
	)

	outsider_queue = as_user(OUTSIDER, lambda: my_tasks("awaiting"))
	check(
		"and it was never in the queue of somebody who holds no step in that chain",
		register_name not in names_in(outsider_queue),
	)

	# The distinction the whole bucket split rests on: my own unsubmitted document is my work,
	# not my queue. workflow.is_own_todo is what draws the line, and this is it drawn.
	requester_awaiting = as_user(REQUESTER, lambda: my_tasks("awaiting"))
	requester_drafts = as_user(REQUESTER, lambda: my_tasks("drafts"))
	check(
		"the requester's own unsubmitted requisition is in Drafts",
		draft.name in names_in(requester_drafts),
		f"{len(names_in(requester_drafts))} drafts",
	)
	check(
		"and not in their approval queue",
		draft.name not in names_in(requester_awaiting),
		f"awaiting: {sorted(names_in(requester_awaiting))[:3]}",
	)

	# Nor in anybody else's. The requester holds `Employee`, and `Employee` is what moves an
	# Activity Requisition out of Draft -- so before the rule was asked of the owner rather than
	# of the viewer, every unfinished draft on the site turned up in every colleague's queue.
	hof_awaiting = as_user(HEAD_OF_FINANCE, lambda: my_tasks("awaiting"))
	check(
		"and not in a colleague's queue either, though they hold the role that submits it",
		draft.name not in names_in(hof_awaiting),
		f"awaiting: {sorted(names_in(hof_awaiting))[:3]}",
	)

	check(
		"the counts agree with the rows returned",
		requester_drafts["counts"]["drafts"]
		== sum(len(group["rows"]) for group in requester_drafts["groups"]),
		f"count {requester_drafts['counts']['drafts']} vs "
		f"{sum(len(g['rows']) for g in requester_drafts['groups'])} rows",
	)

	# Every group says what is being asked. `lane` is allowed to be None and that is not a gap: a
	# turned-down document is at no step, so `step_label` falls back to the state itself
	# ("Rejected"), which is the honest label for it. Inventing a step number there would be
	# claiming it is partway through an approval it has been thrown out of.
	check(
		"queues are grouped by the step being asked for, and say which step that is",
		all(group["step_label"] and group["of"] for group in requester_drafts["groups"]),
		"; ".join(
			f"{g['doctype']} {g['step_label']} "
			f"({'—' if g['lane'] is None else g['lane'] + 1}/{g['of']})"
			for g in requester_drafts["groups"]
		),
	)

	# Derived, not declared: the amount column is whichever Currency field the doctype itself
	# marked in_list_view, so a new module's queue is legible with nothing added to this app.
	amounts = {
		row["currency_field"]
		for group in requester_drafts["groups"]
		for row in group["rows"]
		if row["currency_field"]
	}
	check(
		"and rows carry the amount the doctype itself puts in a list view",
		"budget_amount" in amounts,
		f"currency fields in play: {sorted(amounts)}",
	)

	expect_throw(
		"an unknown bucket is refused rather than quietly returning everything",
		lambda: my_tasks("everything"),
	)

	# The queue spans every workflow doctype, and no role holds a step in all nine -- so building
	# it necessarily asks about doctypes the viewer cannot read. Asking must be silent. It was not:
	# catching the PermissionError from frappe.get_list left the message frappe had already queued
	# on `frappe.local.message_log`, which ships to the browser regardless of who caught what, and
	# a Finance Officer opened My Tasks to three stacked "Insufficient Permission" dialogs over
	# their own perfectly good queue. Asserted for every role, because which doctypes are out of
	# reach differs by role and only some of them showed it.
	noisy = {}
	for user in USERS:
		frappe.local.message_log = []
		as_user(user, lambda: my_tasks("awaiting"))
		if frappe.local.message_log:
			noisy[user] = len(frappe.local.message_log)
	frappe.local.message_log = []

	check(
		"building a queue says nothing about doctypes the viewer cannot read",
		not noisy,
		f"noisy: {noisy}" if noisy else f"{len(USERS)} roles, no messages",
	)

	print("\n--- and it is the first thing staff see ---")

	# Asserted rather than assumed, because the obvious reading of it is wrong: pointing
	# add_to_apps_screen at the page is NOT enough on a site with three apps declaring one.
	# get_default_path() only honours an app's route when the user has exactly one app, so the
	# route needs System Settings.default_app to be consulted at all -- see
	# workspaces.set_landing_page. Silent when it breaks: everybody just lands on /desk again.
	landings = {}
	for user in (REQUESTER, HEAD_OF_PROGRAMS, HEAD_OF_FINANCE, OUTSIDER):
		landings[user] = as_fresh_user(user, get_default_path)

	check(
		"every FoLT role lands on My Tasks after login",
		set(landings.values()) == {f"/desk/{TASKS_PAGE}"},
		"; ".join(f"{u.split('.')[0]}={p}" for u, p in landings.items()),
	)

	check(
		"and setting it again changes nothing",
		set_landing_page() is False,
	)

	# The landing change must not reach the supplier portal: a Website User has no app on the
	# apps screen (supplier_portal.desk_app_visible hides the tile), so get_default_path returns
	# None before it ever reads the setting, and get_home_page keeps them on /rfq.
	suppliers = frappe.get_all(
		"User",
		filters={"user_type": "Website User", "enabled": 1, "name": ("!=", "Guest")},
		pluck="name",
	)
	supplier_landings = {user: as_fresh_user(user, get_default_path) for user in suppliers}
	check(
		"and does not drag portal users into the Desk",
		all(path is None for path in supplier_landings.values()),
		f"{len(suppliers)} website users, paths: {sorted(set(map(str, supplier_landings.values())))}",
	)

	print(f"\n  {len(PASS)} passed, {len(FAIL)} failed")
	if FAIL:
		print("  FAILED: " + "; ".join(FAIL))
		raise SystemExit(1)
	return {"passed": len(PASS), "failed": len(FAIL), "failures": FAIL}
