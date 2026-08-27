"""End-to-end check of FoLT's finance workflow, as set out in the Finance SOP.

The SOP has four steps: the activity requisition and its budget are cleared, the payment
documents are prepared and approved, the payout is made against the participants'
acknowledgement, then the float is accounted for and filed. Steps 3 and 4 used to stop at the
approval decision — nothing tracked whether a float was ever disbursed, retired or written
off, and the retirement claim had no approval route at all. This exercises what the streamlined
chain now does, as the roles that actually do it.

Idempotent: tears down its own fixtures first. Run with:

    bench --site <site> execute folt_customizations.finance_workflow_e2e.run
"""

import frappe
from frappe.model.workflow import apply_workflow, get_transitions
from frappe.utils import add_days, getdate, nowdate

from folt_customizations.float_lifecycle import (
	RETIREMENT_DAYS,
	flag_overdue_floats,
	sync_float_state,
)

PROJECT = "E2E Finance Workflow Activity"
PURPOSE = "E2E finance workflow float"
FLOAT_AMOUNT = 262000
SPENT_AMOUNT = 260000

REQUESTER = "requester.test@folt.test"
FINANCE_OFFICER = "finofficer.test@folt.test"
FINANCE_ASSISTANT = "finassistant.test@folt.test"
HEAD_OF_FINANCE = "hof.test@folt.test"
EXECUTIVE_DIRECTOR = "ed.test@folt.test"

PASS, FAIL = [], []


def check(label, condition, detail=""):
	(PASS if condition else FAIL).append(label)
	print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")


def expect_throw(label, fn):
	try:
		fn()
	except frappe.ValidationError as e:
		check(label, True, str(e)[:90].replace("\n", " "))
		return
	except Exception as e:  # noqa: BLE001
		check(label, False, f"wrong error type: {type(e).__name__}: {e}")
		return
	check(label, False, "no error raised")


def require_users():
	missing = [
		user
		for user in (REQUESTER, FINANCE_OFFICER, FINANCE_ASSISTANT, HEAD_OF_FINANCE, EXECUTIVE_DIRECTOR)
		if not frappe.db.exists("User", user)
	]
	if missing:
		raise SystemExit(f"seed the role test users first (seed_test_users.py): missing {missing}")


def as_user(user, fn):
	"""Run one step as the role that owns it, then hand the session back.

	Every transition below is made by a different person on purpose: an approval chain that
	only works as Administrator proves nothing about whether the Finance Officer can actually
	reach the document.
	"""
	previous = frappe.session.user
	frappe.set_user(user)
	try:
		return fn()
	finally:
		frappe.set_user(previous)


def teardown():
	for name in frappe.get_all(
		"Expense Claim", filters={"remark": ["like", "E2E %"], "docstatus": ["<", 2]}, pluck="name"
	):
		doc = frappe.get_doc("Expense Claim", name)
		if doc.docstatus == 1:
			doc.cancel()
		doc.delete(force=True)

	for name in frappe.get_all(
		"Employee Advance", filters={"purpose": PURPOSE, "docstatus": ["<", 2]}, pluck="name"
	):
		doc = frappe.get_doc("Employee Advance", name)
		if doc.docstatus == 1:
			doc.cancel()
		doc.delete(force=True)

	if frappe.db.exists("Project", PROJECT):
		frappe.delete_doc("Project", PROJECT, force=True, ignore_permissions=True)

	frappe.db.commit()


def setup_activity(end_date):
	"""The activity the float is for. Its end date is what the retirement deadline counts from."""
	return frappe.get_doc(
		{"doctype": "Project", "project_name": PROJECT, "expected_end_date": end_date}
	).insert(ignore_permissions=True)


def company_defaults():
	company = frappe.defaults.get_defaults().get("company") or frappe.get_all("Company", pluck="name")[0]
	return frappe._dict(
		{
			"company": company,
			"advance_account": frappe.db.get_value("Company", company, "default_employee_advance_account"),
			"payable_account": frappe.db.get_value("Company", company, "default_payable_account"),
			"currency": frappe.db.get_value("Company", company, "default_currency"),
			"cost_center": frappe.db.get_value("Company", company, "cost_center"),
		}
	)


def employee_for(user):
	return frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}) or frappe.get_all(
		"Employee", filters={"status": "Active"}, pluck="name"
	)[0]


def make_float(project, defaults, employee):
	return frappe.get_doc(
		{
			"doctype": "Employee Advance",
			"employee": employee,
			"company": defaults.company,
			"posting_date": nowdate(),
			"purpose": PURPOSE,
			"advance_amount": FLOAT_AMOUNT,
			"advance_account": defaults.advance_account,
			"folt_project": project,
			"folt_budget_line": "2.2.2",
		}
	).insert()


def record_disbursement(advance, amount):
	"""Stand in for the Payment Entry of step 3.

	The real thing posts a payment against the advance and ERPNext recomputes paid_amount and
	status from the payment ledger; what matters here is that the status it lands on is the one
	the float's workflow state is derived from.
	"""
	advance.db_set("paid_amount", amount)
	advance.reload()
	advance.set_status(update=True)
	advance.reload()


def expense_claim_type(company):
	"""A claim type that actually has an account on this company.

	hrms throws on validate for a type with no account configured, and the types ERPNext ships
	are mostly unconfigured — so the type has to be chosen by what is set up, not by name.
	"""
	configured = frappe.get_all(
		"Expense Claim Account",
		filters={"company": company, "default_account": ["is", "set"]},
		pluck="parent",
	)
	if not configured:
		raise SystemExit(f"no Expense Claim Type has a default account on {company}")

	return configured[0]


def make_retirement_claim(advance, defaults, employee):
	"""The Float Expense Report of step 4: what was actually spent, against the float."""
	claim = frappe.get_doc(
		{
			"doctype": "Expense Claim",
			"employee": employee,
			"company": defaults.company,
			"posting_date": nowdate(),
			"payable_account": defaults.payable_account,
			"currency": defaults.currency,
			"exchange_rate": 1,
			"remark": "E2E float retirement",
			"project": advance.folt_project,
			"expenses": [
				{
					"expense_date": nowdate(),
					"expense_type": expense_claim_type(defaults.company),
					"description": "E2E transport reimbursement and charges",
					"amount": SPENT_AMOUNT,
					"sanctioned_amount": SPENT_AMOUNT,
					"cost_center": defaults.cost_center,
				}
			],
			"advances": [
				{
					"employee_advance": advance.name,
					"posting_date": advance.posting_date,
					"advance_account": advance.advance_account,
					"advance_paid": advance.paid_amount,
					"unclaimed_amount": advance.paid_amount,
					# What "Get Advances" fills in on the real form. Left unset it defaults to
					# zero, hrms reads that as an exchange loss on the whole allocation and
					# posts a gain/loss Journal Entry -- which the approver has no business
					# creating and no permission to.
					"exchange_rate": advance.get("exchange_rate") or 1,
					"allocated_amount": SPENT_AMOUNT,
				}
			],
		}
	).insert(ignore_permissions=True)
	return claim


def transition_names(doc, user):
	return as_user(user, lambda: {t.action for t in get_transitions(doc)})


def run():
	frappe.set_user("Administrator")
	frappe.flags.mute_emails = True
	require_users()
	teardown()

	defaults = company_defaults()
	employee = employee_for(REQUESTER)
	activity = setup_activity(nowdate())

	print("\n--- W-02  float request, check and approval ---")

	advance = as_user(REQUESTER, lambda: make_float(activity.name, defaults, employee))
	check("float raised by the requester", advance.workflow_state == "Requested", advance.workflow_state)
	check(
		f"accountability deadline stamped {RETIREMENT_DAYS} days after the activity",
		getdate(advance.folt_retire_by) == getdate(add_days(activity.expected_end_date, RETIREMENT_DAYS)),
		f"retire_by={advance.folt_retire_by} activity ends {activity.expected_end_date}",
	)

	as_user(FINANCE_OFFICER, lambda: apply_workflow(advance, "Check"))
	check("Finance Officer checks the float", advance.workflow_state == "Checked", advance.workflow_state)

	as_user(EXECUTIVE_DIRECTOR, lambda: apply_workflow(advance, "Approve"))
	check(
		"Executive Director approves and the float is submitted",
		advance.workflow_state == "Approved" and advance.docstatus == 1,
		f"{advance.workflow_state}/docstatus {advance.docstatus}",
	)

	print("\n--- X-01  segregation of duties on the money decisions ---")

	# Reassigning ownership is the cheapest way to put the approver in the requester's chair.
	frappe.db.set_value("Employee Advance", advance.name, "owner", EXECUTIVE_DIRECTOR)
	advance.reload()
	check(
		"an approver cannot approve a float they raised",
		"Approve" not in transition_names(advance, EXECUTIVE_DIRECTOR),
		f"offered: {sorted(transition_names(advance, EXECUTIVE_DIRECTOR))}",
	)
	frappe.db.set_value("Employee Advance", advance.name, "owner", REQUESTER)
	advance.reload()

	print("\n--- W-03  disbursement, derived rather than clicked ---")

	record_disbursement(advance, FLOAT_AMOUNT)
	check("ERPNext marks the advance paid", advance.status == "Paid", advance.status)

	sync_float_state(advance.name)
	advance.reload()
	check(
		"float state follows the payment to Disbursed with nobody clicking",
		advance.workflow_state == "Disbursed",
		advance.workflow_state,
	)

	print("\n--- X-02  a submitted float belongs to the step it is on ---")

	# `Disbursed` is the Finance Assistant's step (fixtures/workflow.json: allow_edit). Frappe
	# enforces a workflow's transitions and not its `allow_edit` -- that field is read only by the
	# Desk form, which greys the fields out -- so until workflow_access.enforce_state_custodian
	# this document was open to every role holding write on Employee Advance, through the API, a
	# list-view edit or any script. A submitted document is also saved through
	# `before_update_after_submit`, a path the doctype's own validate() never sees at all.
	# The accountability deadline is the field to push on: it is one of only three on Employee
	# Advance that Frappe will accept a change to after submission at all, and W-06 below
	# recomputes it from the activity anyway, so moving it here proves the rule without
	# disturbing what the sweep is then checked on.
	def edit_deadline(user, days):
		def _edit():
			doc = frappe.get_doc("Employee Advance", advance.name)
			doc.folt_retire_by = add_days(nowdate(), days)
			doc.save()

		return lambda: as_user(user, _edit)

	expect_throw(
		"a disbursed float cannot be edited from somebody else's step",
		edit_deadline(FINANCE_OFFICER, 30),
	)
	edit_deadline(FINANCE_ASSISTANT, 20)()
	advance.reload()
	check(
		"the step's own custodian can still edit it",
		getdate(advance.folt_retire_by) == getdate(add_days(nowdate(), 20)),
		str(advance.folt_retire_by),
	)
	# The exemption for transitions is proved further down without any extra checks: the Finance
	# Assistant settles a claim out of the Executive Director's step, and the Head of Finance
	# closes the float out of the Finance Officer's. A move is made by whoever the transition
	# allows, not by whoever holds the step it leaves.

	print("\n--- W-06  the three-day accountability rule ---")

	# The activity is moved into the past rather than the deadline: the sweep recomputes the
	# deadline from the activity, so backdating the field alone would simply be corrected.
	frappe.db.set_value("Project", activity.name, "expected_end_date", add_days(nowdate(), -10))
	flagged = flag_overdue_floats()
	advance.reload()
	check("overdue sweep flags an unaccounted float", advance.name in flagged, f"flagged {len(flagged)}")
	check("float shows as Overdue", advance.workflow_state == "Overdue", advance.workflow_state)
	check(
		"the deadline was recomputed from the activity, not left stale",
		getdate(advance.folt_retire_by) == getdate(add_days(add_days(nowdate(), -10), RETIREMENT_DAYS)),
		str(advance.folt_retire_by),
	)
	check(
		"the move is on the record, not silent",
		bool(
			frappe.db.exists(
				"Comment",
				{"reference_doctype": "Employee Advance", "reference_name": advance.name, "comment_type": "Workflow"},
			)
		),
		"timeline comment written",
	)

	print("\n--- W-05  float retirement: the Float Expense Report ---")

	claim = make_retirement_claim(advance, defaults, employee)
	check("retirement claim opens in Draft", claim.workflow_state == "Draft", str(claim.workflow_state))
	check("approval status starts undecided", claim.approval_status == "Draft", claim.approval_status)

	apply_workflow(claim, "Submit for Review")
	check(
		"claim goes to the Finance Officer",
		claim.workflow_state == "Pending Finance Officer Review",
		claim.workflow_state,
	)

	as_user(FINANCE_OFFICER, lambda: apply_workflow(claim, "Review & Forward"))
	check(
		"Finance Officer checks it and forwards, still unsubmitted",
		claim.workflow_state == "Pending Executive Director Approval" and claim.docstatus == 0,
		f"{claim.workflow_state}/docstatus {claim.docstatus}",
	)

	as_user(EXECUTIVE_DIRECTOR, lambda: apply_workflow(claim, "Approve"))
	claim.reload()
	check(
		"Executive Director approves and the claim is submitted",
		claim.workflow_state == "Approved" and claim.docstatus == 1,
		f"{claim.workflow_state}/docstatus {claim.docstatus}",
	)
	# The one that fails silently if the permlevel grant is missing: frappe reverts a permlevel
	# field the acting user cannot write, and hrms then refuses the submit.
	check(
		"the workflow set approval_status, which is permlevel 1",
		claim.approval_status == "Approved",
		claim.approval_status,
	)

	sync_float_state(advance.name)
	advance.reload()
	check(
		"retiring the claim moves the float to Accounted",
		advance.workflow_state == "Accounted",
		f"{advance.workflow_state} (advance status {advance.status})",
	)

	as_user(FINANCE_ASSISTANT, lambda: apply_workflow(claim, "Mark Settled"))
	check("Finance Assistant settles the balance", claim.workflow_state == "Settled", claim.workflow_state)

	as_user(HEAD_OF_FINANCE, lambda: apply_workflow(advance, "Close Float"))
	check("Head of Finance closes the float", advance.workflow_state == "Closed", advance.workflow_state)

	advance.reload()
	sync_float_state(advance.name)
	advance.reload()
	check(
		"a closed float is not reopened by the derivation",
		advance.workflow_state == "Closed",
		advance.workflow_state,
	)

	print("\n--- workflow wiring ---")

	for workflow, doctype in (
		("Employee Advance Float Approval", "Employee Advance"),
		("FoLT Float Retirement Approval", "Expense Claim"),
		("Participant Reimbursement List Verification", "Participant Reimbursement List"),
	):
		row = frappe.db.get_value("Workflow", workflow, ["document_type", "is_active"], as_dict=True)
		check(
			f"workflow active on {doctype}",
			bool(row and row.is_active and row.document_type == doctype),
			workflow,
		)

	# Every transition that commits money is closed to the person who raised the document.
	guarded = frappe.get_all(
		"Workflow Transition",
		filters={
			"parent": ["in", ["Employee Advance Float Approval", "FoLT Float Retirement Approval", "Participant Reimbursement List Verification"]],
			"action": ["in", ["Check", "Approve", "Review & Forward"]],
		},
		fields=["parent", "action", "allow_self_approval"],
	)
	check(
		"no self-approval on any check or approval step",
		bool(guarded) and not any(row.allow_self_approval for row in guarded),
		f"{len(guarded)} transitions checked",
	)

	frappe.db.rollback()
	teardown()

	print(f"\n{'=' * 60}\nRESULT: {len(PASS)} passed, {len(FAIL)} failed")
	if FAIL:
		for label in FAIL:
			print(f"  FAILED: {label}")
		raise SystemExit(1)
