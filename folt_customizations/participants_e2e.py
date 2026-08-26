"""End-to-end check of the participant chain (Module 1 Annex A, section 6.4).

Exercises the register > reimbursement list derivation and every control that guards it.
Idempotent: tears down its own fixtures first. Run with:

    bench --site <site> execute folt_customizations.participants_e2e.run
"""

import frappe
from frappe.utils import nowdate

PROJECT_A = "E2E Gender Budgeting Public Participation"
PROJECT_B = "E2E Unrelated Activity"
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


def teardown():
	for doctype in (
		"Participant Reimbursement List",
		"Activity Participant List",
	):
		for name in frappe.get_all(doctype, pluck="name"):
			doc = frappe.get_doc(doctype, name)
			if doc.get("activity") in (PROJECT_A, PROJECT_B):
				if doc.docstatus == 1:
					doc.cancel()
				doc.delete(force=True)

	for name in frappe.get_all("FoLT Participant", filters={"participant_name": ["like", "E2E %"]}, pluck="name"):
		frappe.delete_doc("FoLT Participant", name, force=True, ignore_permissions=True)

	for name in frappe.get_all(
		"Employee Advance", filters={"purpose": ["like", "E2E %"], "docstatus": ["<", 2]}, pluck="name"
	):
		frappe.delete_doc("Employee Advance", name, force=True, ignore_permissions=True)

	for project in (PROJECT_A, PROJECT_B):
		if frappe.db.exists("Project", project):
			frappe.delete_doc("Project", project, force=True, ignore_permissions=True)

	frappe.db.commit()


def make_project(name):
	return frappe.get_doc({"doctype": "Project", "project_name": name}).insert(ignore_permissions=True).name


def make_register(project, rows, submit=True):
	doc = frappe.get_doc(
		{
			"doctype": "Activity Participant List",
			"activity": project,
			"session_date": nowdate(),
			"venue": "E2E Venue",
			"attendance_sheet": "/files/e2e-attendance.pdf",
			"participants": rows,
		}
	).insert(ignore_permissions=True)

	if submit:
		doc.submit()

	return doc


def make_float(project, disbursed):
	"""A draft Employee Advance standing in for an approved, disbursed float."""
	employee = frappe.get_all("Employee", filters={"status": "Active"}, pluck="name")
	if not employee:
		raise SystemExit("no active Employee on this site — seed one before running the E2E")

	company = frappe.defaults.get_defaults().get("company") or frappe.get_all("Company", pluck="name")[0]
	account = frappe.db.get_value("Company", company, "default_employee_advance_account") or frappe.get_all(
		"Account", filters={"company": company, "root_type": "Asset", "is_group": 0}, pluck="name"
	)[0]

	advance = frappe.get_doc(
		{
			"doctype": "Employee Advance",
			"employee": employee[0],
			"company": company,
			"posting_date": nowdate(),
			"purpose": "E2E participant reimbursement float",
			"advance_amount": disbursed,
			"advance_account": account,
			"folt_project": project,
		}
	).insert(ignore_permissions=True)

	# Stands in for the Payment Entry that would set this in W-03.
	frappe.db.set_value("Employee Advance", advance.name, "paid_amount", disbursed)
	advance.reload()

	return advance


def run():
	frappe.set_user("Administrator")
	teardown()

	project_a = make_project(PROJECT_A)
	project_b = make_project(PROJECT_B)

	print("\n--- W-04A  attendance register ---")

	register = make_register(
		project_a,
		[
			{"participant_name": "E2E Community One", "mobile_number": "0712000001", "location": "Kanamkemer", "category": "Community Participant"},
			{"participant_name": "E2E Community Two", "mobile_number": "254712000002", "location": "Oropoi", "category": "Community Participant"},
			{"participant_name": "E2E Staff Member", "mobile_number": "0712000003", "location": "Lodwar", "category": "FoLT Staff"},
			{"participant_name": "E2E Absentee", "mobile_number": "0712000004", "location": "Loima", "category": "Community Participant", "attended": 0},
		],
	)

	check("register verified and numbered", register.docstatus == 1 and register.name.startswith("APL-"), register.name)
	check("attendee headcount excludes the absentee", register.total_attendees == 3, f"total_attendees={register.total_attendees}")
	check("staff and absentee are not eligible", register.total_eligible == 2, f"total_eligible={register.total_eligible}")
	check("participant master created from the register", bool(register.participants[0].participant), register.participants[0].participant)
	check("mobile normalised from 254 form", register.participants[1].mobile_number == "0712000002", register.participants[1].mobile_number)

	# F-04A-V4: the same person at a second activity is the same master record.
	register_b = make_register(
		project_b,
		[{"participant_name": "E2E Community One", "mobile_number": "0712000001", "location": "Kanamkemer", "category": "Community Participant"}],
	)
	check(
		"repeat participant matched to the existing master",
		register_b.participants[0].participant == register.participants[0].participant,
		register_b.participants[0].participant,
	)

	expect_throw(
		"F-04A-E2  two attendees sharing a number are rejected",
		lambda: make_register(
			project_a,
			[
				{"participant_name": "E2E Dup A", "mobile_number": "0712000009", "location": "Lodwar", "category": "Community Participant"},
				{"participant_name": "E2E Dup B", "mobile_number": "0712000009", "location": "Lodwar", "category": "Community Participant"},
			],
			submit=False,
		),
	)

	expect_throw(
		"F-04-E3   invalid mobile number is rejected at entry",
		lambda: make_register(
			project_a,
			[{"participant_name": "E2E Bad Number", "mobile_number": "35550755", "location": "Lodwar", "category": "Community Participant"}],
			submit=False,
		),
	)

	expect_throw(
		"F-04A-V1  register cannot be verified without the signed sheet",
		lambda: frappe.get_doc(
			{
				"doctype": "Activity Participant List",
				"activity": project_a,
				"session_date": nowdate(),
				"participants": [{"participant_name": "E2E No Sheet", "mobile_number": "0712000010", "location": "Lodwar", "category": "Community Participant"}],
			}
		).insert(ignore_permissions=True).submit(),
	)

	print("\n--- W-04B  reimbursement list derived from the register ---")

	advance = make_float(project_a, disbursed=20000)
	check("float carries the project", advance.folt_project == project_a, advance.name)

	prl = frappe.get_doc(
		{"doctype": "Participant Reimbursement List", "employee_advance": advance.name}
	).insert(ignore_permissions=True)

	check("project inherited from the float", prl.activity == project_a, prl.activity)

	from folt_customizations.folt_customizations.doctype.participant_reimbursement_list.participant_reimbursement_list import (
		fetch_participants,
	)

	result = fetch_participants(prl.name, register.name)
	prl.reload()

	check("F-04-D1   eligible attendees fetched, not typed", result["added"] == 2, f"added={result['added']}")
	check("F-04-D2   ineligible category skipped", result["skipped_ineligible"] == 1, f"skipped={result['skipped_ineligible']}")
	check("absentee not fetched", len(prl.participants) == 2, f"rows={len(prl.participants)}")
	check(
		"rates proposed from the schedule",
		{row.amount for row in prl.participants} == {3000.0, 8000.0},
		str(sorted(row.amount for row in prl.participants)),
	)
	check("total rolled up", prl.total_amount == 11000, str(prl.total_amount))
	check("each row carries its source register", all(row.source_attendance_list == register.name for row in prl.participants), "")
	check("list records the register it derives from", prl.attendance_reference == register.name, prl.attendance_reference)
	check("rate basis recorded as Schedule", all(row.rate_basis == "Schedule" for row in prl.participants), "")

	def add_off_register_payee():
		doc = frappe.get_doc("Participant Reimbursement List", prl.name)
		doc.append("participants", {"participant_name": "E2E Ghost Payee", "mobile_number": "0712000099", "location": "Lodwar", "transport": 5000})
		doc.save()

	expect_throw("F-04-D4   payee not on a verified register is blocked", add_off_register_payee)

	def adjust_rate_without_reason():
		doc = frappe.get_doc("Participant Reimbursement List", prl.name)
		doc.participants[0].transport = 4500
		doc.save()

	expect_throw("F-04-V3   amount off the schedule needs a reason", adjust_rate_without_reason)

	def adjust_rate_with_reason():
		doc = frappe.get_doc("Participant Reimbursement List", prl.name)
		doc.participants[0].transport = 4500
		doc.participants[0].justification = "Travelled from further out; agreed with Programs."
		doc.save()
		return doc

	adjusted = adjust_rate_with_reason()
	check("F-04-V3   adjusted amount accepted with a reason", adjusted.participants[0].rate_basis == "Adjusted", adjusted.participants[0].rate_basis)

	def mark_paid_without_acknowledgement():
		doc = frappe.get_doc("Participant Reimbursement List", prl.name)
		doc.participants[0].payment_status = "Paid"
		doc.participants[0].acknowledgement = "None"
		doc.save()

	expect_throw("F-04-E4   paid without acknowledgement is blocked", mark_paid_without_acknowledgement)

	def thumbprint_is_valid():
		doc = frappe.get_doc("Participant Reimbursement List", prl.name)
		doc.participants[0].payment_status = "Paid"
		doc.participants[0].acknowledgement = "Thumbprint"
		doc.save()
		return doc

	acked = thumbprint_is_valid()
	check("F-04-E4   thumbprint is valid acknowledgement", acked.participants[0].signed == 1, "signed flag set from acknowledgement")

	def exceed_the_float():
		doc = frappe.get_doc("Participant Reimbursement List", prl.name)
		doc.participants[1].transport = 40000
		doc.participants[1].justification = "Deliberate overrun for the E2E."
		doc.save()

	expect_throw("F-04-E6   list cannot pay out more than the float holds", exceed_the_float)

	print("\n--- project scoping ---")

	def register_from_another_project():
		doc = frappe.get_doc("Participant Reimbursement List", prl.name)
		doc.attendance_reference = register_b.name
		doc.save()

	expect_throw("F-04-D5   register from another project is rejected", register_from_another_project)

	expect_throw(
		"F-04-D5   fetching across projects is rejected",
		lambda: fetch_participants(prl.name, register_b.name),
	)

	print("\n--- verification and payout ---")

	from frappe.model.workflow import apply_workflow

	final = frappe.get_doc("Participant Reimbursement List", prl.name)
	final.participants[0].transport = 3000
	final.participants[0].justification = None
	final.participants[0].payment_status = "Pending"
	final.participants[0].acknowledgement = ""
	final.save()

	apply_workflow(final, "Submit for Review")
	check("W-04B  Draft > Finance review", final.workflow_state == "Pending Finance Officer Review", final.workflow_state)

	apply_workflow(final, "Review & Forward")
	check("W-04B  Finance review > ED approval, still draft", final.workflow_state == "Pending Executive Director Approval" and final.docstatus == 0, f"{final.workflow_state}/docstatus {final.docstatus}")

	apply_workflow(final, "Approve")
	check("W-04B  ED approval > Approved, submitted", final.workflow_state == "Approved" and final.docstatus == 1, f"{final.workflow_state}/docstatus {final.docstatus}")

	# Step 3 of the finance workflow: an approved list goes back to the programme officer for
	# the participants' acknowledgement, and only then is it paid. Acknowledgements and
	# references are therefore recorded against a submitted list, which only works if those
	# fields are editable on submit.
	final.reload()
	for row in final.participants:
		row.payment_status = "Paid"
		row.acknowledgement = "Thumbprint"
		row.payment_reference = f"E2ETXN{row.idx:03d}"
	final.save()
	final.reload()

	check("payout recorded against an approved list", final.total_paid == final.total_amount, f"paid {final.total_paid} of {final.total_amount}")
	check("transaction references captured", all(row.payment_reference for row in final.participants), "")

	expect_throw(
		"F-04-E4  paid list without the acknowledged sheet is refused",
		lambda: apply_workflow(final, "Mark Paid"),
	)

	final.reload()
	final.signed_list = "/files/e2e-signed-reimbursement-list.pdf"
	final.save()
	apply_workflow(final, "Mark Paid")
	check("W-04B  Approved > Paid", final.workflow_state == "Paid", final.workflow_state)

	print("\n--- workflow wiring ---")

	for workflow, doctype in (
		("Activity Participant List Verification", "Activity Participant List"),
		("Participant Reimbursement List Verification", "Participant Reimbursement List"),
	):
		exists = frappe.db.exists("Workflow", workflow)
		active = frappe.db.get_value("Workflow", workflow, "is_active") if exists else 0
		check(f"workflow active on {doctype}", bool(exists and active), workflow)

	check(
		"Employee Advance carries the project link",
		bool(frappe.db.exists("Custom Field", "Employee Advance-folt_project")),
		"folt_project",
	)

	frappe.db.rollback()
	teardown()

	print(f"\n{'=' * 60}\nRESULT: {len(PASS)} passed, {len(FAIL)} failed")
	if FAIL:
		for label in FAIL:
			print(f"  FAILED: {label}")
		raise SystemExit(1)
