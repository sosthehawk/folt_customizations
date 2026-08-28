"""End-to-end check of the activity float chain's hand-offs (activity_chain.py).

finance_workflow_e2e.py proves the approval chains move. This proves the joins between them: that
an approved requisition fills the float request, that the register comes out headed for the right
activity, that the reimbursement list arrives with its payees and rates already in it, that the
retirement claim accounts for what was actually paid, and that none of those can be taken before
the document behind them has been approved.

Every step is made by the role that makes it in real life, so a hand-off that only works as
Administrator fails here.

Idempotent: tears down its own fixtures first. Run with:

    bench --site <site> execute folt_customizations.activity_chain_e2e.run
"""

import frappe
from frappe.model.workflow import apply_workflow
from frappe.utils import add_days, flt, getdate, nowdate

from folt_customizations import activity_chain
from folt_customizations.float_lifecycle import RETIREMENT_DAYS, sync_float_state

ACTIVITY = "E2E Chain Public Participation on Gender Budgeting"
BUDGET = 300000
FLOAT = 262000
BUDGET_LINE = "2.2.2"
VENUE = "E2E Cradle Hotel"

REQUESTER = "requester.test@folt.test"
HEAD_OF_PROGRAMS = "hop.test@folt.test"
HEAD_OF_FINANCE = "hof.test@folt.test"
FINANCE_OFFICER = "finofficer.test@folt.test"
FINANCE_ASSISTANT = "finassistant.test@folt.test"

# Spelled out because the `modified` assertion below reads the row directly rather than through
# the loaded document, whose own timestamp is whatever it was when it was last fetched.
ADVANCE_DOCTYPE = "Employee Advance"
EXECUTIVE_DIRECTOR = "ed.test@folt.test"

USERS = (
	REQUESTER,
	HEAD_OF_PROGRAMS,
	HEAD_OF_FINANCE,
	FINANCE_OFFICER,
	FINANCE_ASSISTANT,
	EXECUTIVE_DIRECTOR,
)

# Two locations off the provisional rate schedule, so the amounts the fetch proposes are the
# scheduled ones and no row needs a justification. A third attendee who is FoLT staff, to prove
# the list is filtered by eligibility rather than merely copied.
ATTENDEES = [
	{"participant_name": "E2E Chain Kanamkemer", "mobile_number": "0712900001", "location": "Kanamkemer", "category": "Community Participant"},
	{"participant_name": "E2E Chain Nakalale", "mobile_number": "0712900002", "location": "Nakalale", "category": "Community Participant"},
	{"participant_name": "E2E Chain Staffer", "mobile_number": "0712900003", "location": "Lodwar", "category": "FoLT Staff"},
]
EXPECTED_PAYOUT = 3000 + 4000

PASS, FAIL = [], []


def check(label, condition, detail=""):
	(PASS if condition else FAIL).append(label)
	print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")


def expect_throw(label, fn, exc=frappe.ValidationError):
	"""`exc` because frappe.PermissionError does not descend from frappe.ValidationError, so a
	permission check asserted with the default reports "wrong error type" while the code is right."""
	try:
		fn()
	except exc as e:
		check(label, True, str(e)[:100].replace("\n", " "))
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


def require_users():
	missing = [user for user in USERS if not frappe.db.exists("User", user)]
	if missing:
		raise SystemExit(f"seed the role test users first (seed_test_users.py): missing {missing}")


def teardown():
	for doctype, filters in (
		("Expense Claim", {"remark": ["like", f"%{ACTIVITY}%"]}),
		("Participant Reimbursement List", {"activity": ACTIVITY}),
		("Activity Participant List", {"activity": ACTIVITY}),
		("Employee Advance", {"purpose": ["like", f"%{ACTIVITY}%"]}),
		("Activity Requisition", {"activity_program": ACTIVITY}),
	):
		for name in frappe.get_all(doctype, filters={**filters, "docstatus": ["<", 2]}, pluck="name"):
			doc = frappe.get_doc(doctype, name)
			if doc.docstatus == 1:
				doc.cancel()
			doc.delete(force=True)

	for name in frappe.get_all(
		"FoLT Participant", filters={"participant_name": ["like", "E2E Chain %"]}, pluck="name"
	):
		frappe.delete_doc("FoLT Participant", name, force=True, ignore_permissions=True)

	if frappe.db.exists("Project", ACTIVITY):
		frappe.delete_doc("Project", ACTIVITY, force=True, ignore_permissions=True)

	frappe.db.commit()


def employee_for(user):
	return frappe.db.get_value("Employee", {"user_id": user, "status": "Active"})


def float_holder():
	"""Somebody other than the requester, because a float is often carried by a third person."""
	requester = employee_for(REQUESTER)
	other = frappe.get_all(
		"Employee", filters={"status": "Active", "name": ["!=", requester]}, pluck="name"
	)
	return other[0] if other else requester


def make_requisition(holder):
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
			"float_required": 1,
			"float_holder": holder,
			"float_amount": FLOAT,
			"description": "E2E chain: transport reimbursement for public participation",
		}
	).insert()


def record_disbursement(advance, amount):
	"""Stands in for step 3's Payment Entry, as finance_workflow_e2e does."""
	advance.db_set("paid_amount", amount)
	advance.reload()
	advance.set_status(update=True)
	advance.reload()
	sync_float_state(advance.name)
	advance.reload()


def mark_paid_without_signed_list(name):
	"""Take the signed sheet away and try to mark the list paid, to prove the rule bites."""
	frappe.db.set_value(
		"Participant Reimbursement List", name, "signed_list", None, update_modified=False
	)
	doc = frappe.get_doc("Participant Reimbursement List", name)
	try:
		apply_workflow(doc, "Mark Paid")
	finally:
		frappe.db.set_value(
			"Participant Reimbursement List",
			name,
			"signed_list",
			"/files/e2e-chain-signed-list.pdf",
			update_modified=False,
		)


def run():
	frappe.set_user("Administrator")
	frappe.flags.mute_emails = True
	require_users()
	teardown()

	holder = float_holder()

	print("\n--- step 1  the requisition, and the activity it opens ---")

	requisition = as_user(REQUESTER, lambda: make_requisition(holder))
	check(
		"requisition raised and defaulted",
		requisition.workflow_state == "Draft" and getdate(requisition.activity_end_date) == getdate(add_days(nowdate(), 1)),
		f"{requisition.name} / {requisition.workflow_state}",
	)

	expect_throw(
		"no float can be raised from an unapproved requisition",
		lambda: as_user(REQUESTER, lambda: activity_chain.make_float_request(requisition.name)),
	)

	as_user(REQUESTER, lambda: apply_workflow(requisition, "Submit for Review"))
	as_user(HEAD_OF_PROGRAMS, lambda: apply_workflow(requisition, "Approve"))
	as_user(HEAD_OF_FINANCE, lambda: apply_workflow(requisition, "Approve"))
	requisition.reload()
	check(
		"Head of Programs then Head of Finance approve it",
		requisition.workflow_state == "Approved" and requisition.docstatus == 1,
		f"{requisition.workflow_state}/docstatus {requisition.docstatus}",
	)
	check(
		"approval opened the activity's Project",
		bool(requisition.project)
		and frappe.db.get_value("Project", requisition.project, "project_name") == ACTIVITY,
		str(requisition.project),
	)
	check(
		"the Project carries the activity's end date, which the float's deadline counts from",
		getdate(frappe.db.get_value("Project", requisition.project, "expected_end_date"))
		== getdate(requisition.activity_end_date),
		str(frappe.db.get_value("Project", requisition.project, "expected_end_date")),
	)

	print("\n--- step 1 -> step 2  the Float Request Form fills itself ---")

	advance_name = as_user(REQUESTER, lambda: activity_chain.make_float_request(requisition.name))
	advance = frappe.get_doc("Employee Advance", advance_name)
	check("float request opens in Requested", advance.workflow_state == "Requested", advance.workflow_state)
	check(
		"amount, budget line and donor carried from the requisition",
		flt(advance.advance_amount) == FLOAT and advance.folt_budget_line == BUDGET_LINE,
		f"{advance.advance_amount} / {advance.folt_budget_line}",
	)
	check(
		"the activity carried, so the list and the register can be scoped by it",
		advance.folt_project == requisition.project,
		str(advance.folt_project),
	)
	check(
		"the float is in the holder's name, not the requester's",
		advance.employee == holder and holder != employee_for(REQUESTER),
		f"{advance.employee} vs requester {employee_for(REQUESTER)}",
	)
	check(
		"the requisition is on the float, so the chain is traceable back",
		advance.folt_activity_requisition == requisition.name,
		str(advance.folt_activity_requisition),
	)
	check(
		f"accountability deadline stamped {RETIREMENT_DAYS} days after the activity ends",
		getdate(advance.folt_retire_by) == getdate(add_days(requisition.activity_end_date, RETIREMENT_DAYS)),
		str(advance.folt_retire_by),
	)

	print("\n--- step 2 -> step 3  check, approve, disburse ---")

	as_user(FINANCE_OFFICER, lambda: apply_workflow(advance, "Check"))
	as_user(EXECUTIVE_DIRECTOR, lambda: apply_workflow(advance, "Approve"))

	before_derived = frappe.db.get_value(ADVANCE_DOCTYPE, advance.name, "modified")
	record_disbursement(advance, FLOAT)
	check(
		"the float reaches Disbursed with nobody clicking it",
		advance.workflow_state == "Disbursed",
		advance.workflow_state,
	)
	# A derived state has to move `modified` with it. float_lifecycle._apply used to pass
	# update_modified=False, which meant a float could change state with no token that changed --
	# so Frappe's optimistic lock passed on a form loaded before the move and the stale state went
	# back to the server. Nothing else in this suite would notice the flag being put back.
	check(
		"and the derived state moves `modified`, so a stale client cannot overwrite it",
		frappe.db.get_value(ADVANCE_DOCTYPE, advance.name, "modified") != before_derived,
		f"{before_derived} -> {frappe.db.get_value(ADVANCE_DOCTYPE, advance.name, 'modified')}",
	)

	print("\n--- step 1 -> step 4  the attendance register, headed and empty ---")

	register_name = as_user(REQUESTER, lambda: activity_chain.make_attendance_register(requisition.name))
	register = frappe.get_doc("Activity Participant List", register_name)
	check(
		"register opened on the activity, its title, date and venue",
		register.activity == requisition.project
		and register.activity_title == ACTIVITY
		and getdate(register.session_date) == getdate(requisition.activity_date)
		and register.venue == VENUE,
		f"{register.name} / {register.session_date} / {register.venue}",
	)
	check(
		"attendees are left to the day — nothing invented",
		not register.participants and register.activity_requisition == requisition.name,
		f"{len(register.participants)} rows",
	)

	expect_throw(
		"no reimbursement list can be derived from an unverified register",
		lambda: as_user(REQUESTER, lambda: activity_chain.make_reimbursement_list(register.name)),
	)

	def fill_and_verify():
		doc = frappe.get_doc("Activity Participant List", register.name)
		for row in ATTENDEES:
			doc.append("participants", row)
		doc.attendance_sheet = "/files/e2e-chain-attendance.pdf"
		doc.save()
		apply_workflow(doc, "Submit for Verification")
		return doc

	register = as_user(REQUESTER, fill_and_verify)
	as_user(HEAD_OF_PROGRAMS, lambda: apply_workflow(register, "Verify"))
	register.reload()
	check(
		"Head of Programs verifies the register",
		register.workflow_state == "Verified" and register.docstatus == 1,
		f"{register.workflow_state}/docstatus {register.docstatus}",
	)

	verified_status = as_user(
		REQUESTER, lambda: activity_chain.get_chain_status("Activity Participant List", register.name)
	)
	check(
		"a verified register offers the reimbursement list",
		verified_status["handoffs"][0]["ready"]
		and verified_status["handoffs"][0]["target"] == "Participant Reimbursement List",
		f"ready={verified_status['handoffs'][0]['ready']}",
	)

	print("\n--- step 4 -> step 5  the reimbursement list arrives filled in ---")

	result = as_user(REQUESTER, lambda: activity_chain.make_reimbursement_list(register.name))
	check(
		"the float was resolved from the activity, not asked for",
		not result.get("needs_float") and bool(result.get("name")),
		str(result),
	)

	reimbursement = frappe.get_doc("Participant Reimbursement List", result["name"])
	check(
		"list scoped to the activity and the float in one action",
		reimbursement.activity == requisition.project
		and reimbursement.employee_advance == advance.name
		and reimbursement.attendance_reference == register.name,
		f"{reimbursement.name}",
	)
	check(
		"only eligible attendees were pulled through",
		result["added"] == 2 and result["skipped_ineligible"] == 1,
		f"added {result['added']}, skipped {result['skipped_ineligible']}",
	)
	check(
		"scheduled rates proposed, so no amount was typed",
		flt(reimbursement.total_amount) == EXPECTED_PAYOUT
		and all(row.rate_basis == "Schedule" for row in reimbursement.participants),
		f"total {reimbursement.total_amount}, bases {[r.rate_basis for r in reimbursement.participants]}",
	)
	check(
		"the float's disbursed balance is on the list it is paid out of",
		flt(reimbursement.advance_disbursed) == FLOAT,
		str(reimbursement.advance_disbursed),
	)

	expect_throw(
		"a second list cannot be derived from the same register",
		lambda: as_user(REQUESTER, lambda: activity_chain.make_reimbursement_list(register.name)),
	)

	print("\n--- the float list is not readable by everybody who can guess a project ---")

	# funded_floats is whitelisted and used to be a bare frappe.get_all, so any logged-in session
	# -- a supplier's portal login included -- could name a project and get employee names and
	# float amounts back. Both directions are asserted because a permission fix that also breaks
	# the people who need the data is not a fix: the requester raises the reimbursement list and
	# has to be able to resolve the float behind it.
	visible = as_user(REQUESTER, lambda: activity_chain.funded_floats(requisition.project))
	check(
		"the requester who raises the list still sees the float it pays from",
		[row["name"] for row in visible] == [advance.name],
		f"{[row['name'] for row in visible]}",
	)

	expect_throw(
		"a role with no Employee Advance read is refused, not handed an empty list",
		lambda: as_user(HEAD_OF_PROGRAMS, lambda: activity_chain.funded_floats(requisition.project)),
		exc=frappe.PermissionError,
	)

	print("\n--- step 5  approval and payout ---")

	as_user(REQUESTER, lambda: apply_workflow(reimbursement, "Submit for Review"))
	as_user(FINANCE_OFFICER, lambda: apply_workflow(reimbursement, "Review & Forward"))
	as_user(EXECUTIVE_DIRECTOR, lambda: apply_workflow(reimbursement, "Approve"))
	reimbursement.reload()
	check(
		"Finance Officer forwards, Executive Director approves",
		reimbursement.workflow_state == "Approved" and reimbursement.docstatus == 1,
		f"{reimbursement.workflow_state}/docstatus {reimbursement.docstatus}",
	)

	expect_throw(
		"a float cannot be retired against a list that has not been paid",
		lambda: as_user(FINANCE_OFFICER, lambda: activity_chain.make_float_retirement(reimbursement.name)),
	)

	# An approved list's next move is the payout, and the payout is the Finance Assistant's --
	# so `Approved` is their step to hold. It used to be the Finance Officer's, which made the
	# payout impossible to record: `apply_workflow` reloads the document from the database
	# (frappe/model/workflow.py:123) and discards whatever came with the action, so the
	# acknowledgements and the signed sheet have to be saved *before* Mark Paid -- a save only
	# the custodian of the step may make, and only the Finance Assistant may Mark Paid.
	def record_payout():
		doc = frappe.get_doc("Participant Reimbursement List", reimbursement.name)
		for row in doc.participants:
			row.payment_status = "Paid"
			row.acknowledgement = "Signature"
			row.payment_reference = f"E2ECHAIN{row.idx:03d}"
		doc.signed_list = "/files/e2e-chain-signed-list.pdf"
		doc.save()
		return doc

	paid = as_user(FINANCE_ASSISTANT, record_payout)
	check(
		"the Finance Assistant can record the payout on the step they hold",
		flt(paid.total_paid) == EXPECTED_PAYOUT,
		f"paid {paid.total_paid} of {paid.total_amount}",
	)

	expect_throw(
		"marking a list paid needs the participants' signed acknowledgement",
		lambda: as_user(FINANCE_ASSISTANT, lambda: mark_paid_without_signed_list(reimbursement.name)),
	)

	as_user(FINANCE_ASSISTANT, lambda: apply_workflow(paid, "Mark Paid"))
	reimbursement.reload()
	check(
		"Finance Assistant marks the list paid against the signed list",
		reimbursement.workflow_state == "Paid" and flt(reimbursement.total_paid) == EXPECTED_PAYOUT,
		f"{reimbursement.workflow_state}, paid {reimbursement.total_paid}",
	)

	print("\n--- step 5 -> step 6  the Float Expense Report ---")

	retirement = as_user(FINANCE_OFFICER, lambda: activity_chain.make_float_retirement(reimbursement.name))
	claim = frappe.get_doc("Expense Claim", retirement["name"])
	check(
		"retirement claim opens in Draft, in the float holder's name",
		claim.workflow_state == "Draft" and claim.employee == advance.employee,
		f"{claim.name} / {claim.workflow_state} / {claim.employee}",
	)
	check(
		"one expense row per paid list, at what was actually paid",
		len(claim.expenses) == 1 and flt(claim.expenses[0].amount) == EXPECTED_PAYOUT,
		f"{len(claim.expenses)} rows, {claim.expenses[0].amount}",
	)
	check(
		"the float is allocated on the claim, with an exchange rate",
		len(claim.advances) == 1
		and claim.advances[0].employee_advance == advance.name
		and flt(claim.advances[0].allocated_amount) == EXPECTED_PAYOUT
		and flt(claim.advances[0].exchange_rate) == 1,
		f"allocated {claim.advances[0].allocated_amount}, rate {claim.advances[0].exchange_rate}",
	)
	check(
		"the balance still owed on the float is arithmetic, not a re-count",
		flt(retirement["balance"]) == FLOAT - EXPECTED_PAYOUT,
		f"float {retirement['float_paid']} - spent {retirement['spent']} = {retirement['balance']}",
	)
	check(
		"the list is on the claim, so the same payout cannot be retired twice",
		claim.folt_reimbursement_list == reimbursement.name,
		str(claim.folt_reimbursement_list),
	)

	expect_throw(
		"the same payout cannot be retired a second time",
		lambda: as_user(FINANCE_OFFICER, lambda: activity_chain.make_float_retirement(reimbursement.name)),
	)

	print("\n--- the banner: what the person on the form is told ---")

	status = as_user(FINANCE_OFFICER, lambda: activity_chain.get_chain_status("Employee Advance", advance.name))
	check(
		"the float says where it is in the SOP",
		status["step"] == 2 and status["of"] == activity_chain.CHAIN_LENGTH,
		f"step {status['step']} of {status['of']}: {status['step_title']}",
	)

	status = as_user(REQUESTER, lambda: activity_chain.get_chain_status("Activity Requisition", requisition.name))
	handoffs = {row["label"]: row for row in status["handoffs"]}
	check(
		"an approved requisition offers both next documents",
		handoffs["Float Request"]["ready"] and handoffs["Attendance Register"]["ready"],
		str({k: v["ready"] for k, v in handoffs.items()}),
	)
	check(
		"and shows what has already been raised from it",
		handoffs["Float Request"]["existing"] == [advance.name]
		and handoffs["Attendance Register"]["existing"] == [register.name],
		f"{handoffs['Float Request']['existing']} / {handoffs['Attendance Register']['existing']}",
	)

	status = as_user(REQUESTER, lambda: activity_chain.get_chain_status("Activity Participant List", register.name))
	check(
		"a register that has already been derived stops offering the button",
		not status["handoffs"][0]["ready"] and status["handoffs"][0]["existing"] == [reimbursement.name],
		f"ready={status['handoffs'][0]['ready']}, existing={status['handoffs'][0]['existing']}",
	)
	waiting = as_user(
		FINANCE_OFFICER, lambda: activity_chain.get_chain_status("Employee Advance", advance.name)
	)
	check(
		"a disbursed float names who is waited on next",
		waiting["waiting_for"] == ["Finance Officer"] or "Finance Officer" in waiting["waiting_for"],
		str(waiting["waiting_for"]),
	)

	print("\n--- a requisition with no float offers no float request ---")

	def no_float_requisition():
		doc = make_requisition(holder)
		doc.activity_program = f"{ACTIVITY} (no float)"
		doc.float_required = 0
		doc.float_holder = None
		doc.float_amount = 0
		doc.save()
		apply_workflow(doc, "Submit for Review")
		return doc

	dry = as_user(REQUESTER, no_float_requisition)
	as_user(HEAD_OF_PROGRAMS, lambda: apply_workflow(dry, "Approve"))
	as_user(HEAD_OF_FINANCE, lambda: apply_workflow(dry, "Approve"))
	dry_status = as_user(REQUESTER, lambda: activity_chain.get_chain_status("Activity Requisition", dry.name))
	dry_handoffs = {row["label"]: row for row in dry_status["handoffs"]}
	check(
		"no cash float means no Float Request button",
		not dry_handoffs["Float Request"]["ready"] and dry_handoffs["Attendance Register"]["ready"],
		str({k: v["ready"] for k, v in dry_handoffs.items()}),
	)
	expect_throw(
		"and the maker refuses it on the server too",
		lambda: as_user(REQUESTER, lambda: activity_chain.make_float_request(dry.name)),
	)

	expect_throw(
		"a float larger than the activity's budget is refused",
		lambda: as_user(
			REQUESTER,
			lambda: frappe.get_doc(
				{
					"doctype": "Activity Requisition",
					"activity_program": f"{ACTIVITY} (over budget)",
					"requested_by": employee_for(REQUESTER),
					"activity_date": nowdate(),
					"company": requisition.company,
					"budget_amount": 1000,
					"budget_line": BUDGET_LINE,
					"float_required": 1,
					"float_holder": holder,
					"float_amount": 5000,
				}
			).insert(),
		),
	)

	frappe.db.rollback()
	teardown()
	for name in frappe.get_all(
		"Project", filters={"project_name": ["like", f"{ACTIVITY}%"]}, pluck="name"
	):
		frappe.delete_doc("Project", name, force=True, ignore_permissions=True)
	for name in frappe.get_all(
		"Activity Requisition", filters={"activity_program": ["like", f"{ACTIVITY}%"], "docstatus": ["<", 2]}, pluck="name"
	):
		doc = frappe.get_doc("Activity Requisition", name)
		if doc.docstatus == 1:
			doc.cancel()
		doc.delete(force=True)
	frappe.db.commit()

	print(f"\n{'=' * 60}\nRESULT: {len(PASS)} passed, {len(FAIL)} failed")
	if FAIL:
		for label in FAIL:
			print(f"  FAILED: {label}")
		raise SystemExit(1)
