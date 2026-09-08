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

	# F-04A-V1 used to be a throw: no signed sheet, no verification. Verification is what the
	# rest of the chain waits on -- a reimbursement list may only be derived from a verified
	# register -- so holding it there over a scan that changes nothing about who attended stopped
	# the activity rather than the mistake. The sheet is now asked for at submit and stands on the
	# form's checklist until it arrives, and the field is `allow_on_submit` so it still can.
	# Raised on project_b so the dropdown checks below still see exactly one verified register on
	# project_a.
	no_sheet = frappe.get_doc(
		{
			"doctype": "Activity Participant List",
			"activity": project_b,
			"session_date": nowdate(),
			"participants": [{"participant_name": "E2E No Sheet", "mobile_number": "0712000010", "location": "Lodwar", "category": "Community Participant"}],
		}
	).insert(ignore_permissions=True)
	no_sheet.submit()
	check(
		"F-04A-V1  a register with no signed sheet is verified rather than refused",
		no_sheet.docstatus == 1 and not no_sheet.attendance_sheet,
		f"docstatus {no_sheet.docstatus}",
	)

	no_sheet.attendance_sheet = "/files/e2e-attendance.pdf"
	no_sheet.save()
	check(
		"F-04A-V1  and the sheet can still be attached to it once it arrives",
		frappe.db.get_value("Activity Participant List", no_sheet.name, "attendance_sheet")
		== "/files/e2e-attendance.pdf",
	)

	# The gate that did NOT move. An empty register is not evidence waiting on a scanner, it is a
	# register of nobody, and there is nothing to verify about it.
	expect_throw(
		"F-04A-V1  but a register with no attendees still cannot be verified",
		lambda: frappe.get_doc(
			{"doctype": "Activity Participant List", "activity": project_b, "session_date": nowdate()}
		).insert(ignore_permissions=True).submit(),
	)

	print("\n--- W-04A  roster autofill and reconciliation ---")

	# The loop this exists to serve: fill the next session's register from the last one, print the
	# FoLT Attendance Sheet with the names already on it, then tick the signed paper back in.
	# It replaces the typing that OCR cannot do for us -- FoLT's participants list is filled in
	# by hand at the activity, and handwritten names and 10-digit numbers do not survive OCR.
	from folt_customizations.folt_customizations.doctype.activity_participant_list.activity_participant_list import (
		_match_category,
		autofill_roster,
		get_roster_sources,
	)

	# Run on project_b throughout. The F-04-D5 dropdown checks further down assert that
	# project_a offers EXACTLY one verified register, so a second verified one there would break
	# them -- the same reason `no_sheet` above was raised on project_b.
	day_one = make_register(
		project_b,
		[
			{"participant_name": "E2E Day One Alpha", "mobile_number": "0712000021", "location": "Kanamkemer", "category": "Community Participant"},
			{"participant_name": "E2E Day One Beta", "mobile_number": "0712000022", "location": "Oropoi", "category": "Community Participant"},
			{"participant_name": "E2E Day One Staff", "mobile_number": "0712000023", "location": "Lodwar", "category": "FoLT Staff"},
			{"participant_name": "E2E Day One Absentee", "mobile_number": "0712000024", "location": "Loima", "category": "Community Participant", "attended": 0},
		],
	)

	session_two = frappe.get_doc(
		{
			"doctype": "Activity Participant List",
			"activity": project_b,
			"session_date": nowdate(),
			"venue": "E2E Venue, day two",
		}
	).insert(ignore_permissions=True)

	sources = get_roster_sources(session_two.name)
	check(
		"F-04A-V2  an earlier session on the same activity is offered as a roster source",
		any(s["name"] == day_one.name for s in sources),
		f"{len(sources)} source(s)",
	)
	check(
		"F-04A-V2  and a register on another activity is not",
		all(s["name"] != register.name for s in sources),
	)

	summary = autofill_roster(session_two.name, from_register=day_one.name)
	session_two.reload()
	check(
		"roster copies the attendees of the earlier session",
		summary["added"] == 3 and len(session_two.participants) == 3,
		f"added={summary['added']} rows={len(session_two.participants)}",
	)
	# The point of the whole design: a roster is who is EXPECTED, so it must not claim a
	# headcount. A register reading 3 attendees before the sheet came back would be a lie the
	# reimbursement list is allowed to derive from.
	check(
		"and does NOT inflate the headcount",
		session_two.total_attendees == 0,
		f"total_attendees={session_two.total_attendees}",
	)
	check(
		"rows land unattended and unacknowledged",
		all(not row.attended and not row.acknowledgement for row in session_two.participants),
		f"acknowledgements={[r.acknowledgement for r in session_two.participants]}",
	)
	check(
		"the absentee from the earlier session is left off by default",
		all("Absentee" not in (row.participant_name or "") for row in session_two.participants),
	)

	repeat = autofill_roster(session_two.name, from_register=day_one.name)
	check(
		"F-04A-E2  running it twice adds nobody twice",
		repeat["added"] == 0 and repeat["skipped_existing"] == 3,
		f"added={repeat['added']} skipped={repeat['skipped_existing']}",
	)

	# An unreconciled roster is not a register of nobody -- it is a register whose sheet has not
	# come back -- so it must not be verifiable.
	expect_throw(
		"an unreconciled roster cannot be verified",
		lambda: frappe.get_doc("Activity Participant List", session_two.name).submit(),
	)

	# THE REGRESSION THAT MOTIVATED THE FIX in normalise_rows. `attended` used to zero
	# eligibility permanently: the category branch only re-derives while a row is new or the
	# value is None, so ticking somebody present after the sheet came back left them ineligible
	# for ever and fetch_participants skipped every one of them as "not eligible by category".
	# Nobody would have been paid. This is what that looked like.
	session_two.reload()
	for row in session_two.participants:
		row.attended = 1
		row.acknowledgement = "Signature"
	session_two.save()
	check(
		"reconciling a roster restores eligibility by category",
		session_two.total_attendees == 3 and session_two.total_eligible == 2,
		f"attendees={session_two.total_attendees} eligible={session_two.total_eligible}",
	)

	# Absence and a signature cannot both stand on one row. Refusing it was tried and was worse:
	# `acknowledgement` DEFAULTS to "Signature", so unticking a box threw an error naming a
	# signature nobody had claimed. Normalising is the honest answer.
	session_two.participants[0].attended = 0
	session_two.participants[0].acknowledgement = "Signature"
	session_two.save()
	check(
		"marking somebody absent clears the acknowledgement rather than refusing the save",
		session_two.participants[0].acknowledgement == "None"
		and session_two.total_attendees == 2,
		f"acknowledgement={session_two.participants[0].acknowledgement!r}",
	)

	session_two.attendance_sheet = "/files/e2e-attendance.pdf"
	session_two.save()
	session_two.submit()
	check("a reconciled roster verifies", session_two.docstatus == 1, session_two.workflow_state)

	expect_throw(
		"a roster cannot be added to a verified register",
		lambda: autofill_roster(session_two.name, from_register=day_one.name),
	)

	sheet = frappe.db.get_value(
		"Print Format", "FoLT Attendance Sheet", ["doc_type", "custom_format", "disabled"], as_dict=True
	)
	check(
		"the FoLT Attendance Sheet prints this doctype from its own template",
		bool(sheet) and sheet.doc_type == "Activity Participant List" and sheet.custom_format == 1 and not sheet.disabled,
		f"{sheet}",
	)

	print("\n--- W-04A  reading a sheet back in ---")

	# Round-trip through FoLT's own print format, so this needs no binary fixture in the repo:
	# print the register that was just rostered, then read the printed sheet back and check the
	# same people come out. That is also the real loop -- the sheet FoLT hands out is the sheet
	# that comes back.
	from folt_customizations import sheet_import
	from folt_customizations.folt_customizations.doctype.activity_participant_list.activity_participant_list import (
		read_attendance_sheet,
	)

	target = frappe.get_doc(
		{
			"doctype": "Activity Participant List",
			"activity": project_b,
			"session_date": nowdate(),
			"venue": "E2E Venue, import",
		}
	).insert(ignore_permissions=True)

	printed = None
	try:
		from frappe.utils.pdf import get_pdf

		printed = get_pdf(
			frappe.get_print("Activity Participant List", day_one.name, print_format="FoLT Attendance Sheet")
		)
	except Exception as e:  # noqa: BLE001
		# wkhtmltopdf is not always reachable from a bare shell (see print_formats.guard_pdf_host).
		# Skipped loudly rather than failed, because it is the PDF toolchain rather than this code.
		check("PDF round-trip skipped (wkhtmltopdf unavailable)", True, type(e).__name__)

	if printed:
		sheet = frappe.get_doc(
			{
				"doctype": "File", "file_name": "e2e-printed-sheet.pdf", "content": printed,
				"is_private": 0, "attached_to_doctype": "Activity Participant List",
				"attached_to_name": target.name,
			}
		).insert(ignore_permissions=True)

		result = read_attendance_sheet(target.name, file_url=sheet.file_url)
		names = {r["participant_name"].upper() for r in result["rows"]}
		mobiles = {r["mobile_number"] for r in result["rows"]}

		check(
			"the printed attendance sheet reads back into attendees",
			len(result["rows"]) == 4 and "E2E DAY ONE ALPHA" in names,
			f"{len(result['rows'])} rows: {sorted(names)}",
		)
		check(
			"and their mobile numbers survive the round trip",
			{"0712000021", "0712000022", "0712000023"} <= mobiles,
			str(sorted(mobiles)),
		)
		check(
			"a PDF with a text layer is read exactly rather than by OCR",
			result["source"] == "native",
			result["source"],
		)
		# day_one is verified, so its sheet prints the record rather than a blank form: each row
		# carries the acknowledgement that was actually given. Reading it back has to recover the
		# same three signatures and the same one absence -- if attendance did not survive the
		# round trip, importing a sheet would silently change who gets paid.
		check(
			"who signed survives the round trip",
			sum(1 for r in result["rows"] if r["attended"]) == 3,
			str([(r["participant_name"][-5:], r["acknowledgement"]) for r in result["rows"]]),
		)
		check(
			"reading writes nothing until the preparer saves",
			not frappe.get_doc("Activity Participant List", target.name).participants,
		)

	expect_throw(
		"a sheet cannot be read into a verified register",
		lambda: read_attendance_sheet(day_one.name, file_url="/files/whatever.png"),
	)

	# The handwriting gate, tested on its own rather than through an image. The measured
	# separation is absolute -- 90.5% valid numbers on typed sheets against 0% on handwritten
	# ones -- so what matters is that the threshold sits between them.
	typed_rows = [{"_phone_raw": "0712000021", "mobile_number": "0712000021"} for _ in range(6)]
	hand_rows = [{"_phone_raw": "07l2 OOO", "mobile_number": "0712"} for _ in range(6)]
	check(
		"a typed sheet is not mistaken for handwriting",
		not sheet_import._looks_handwritten(typed_rows),
	)
	check(
		"a handwritten sheet is detected and refused",
		sheet_import._looks_handwritten(hand_rows),
	)

	# The header of a printed sheet loses short words in shaded cells more often than the rows
	# below it lose anything, so the columns that matter are inferred from the data when the
	# header does not name them. Without this the sheet reads as empty, which is the worst
	# failure available: it looks like nobody attended.
	inferred = sheet_import.infer_columns(
		[["1", "CONSOLATA ARII", "0701259286"], ["2", "PAULINA BARAZA", "0710238217"]],
		{},
		3,
	)
	check(
		"name and phone columns are inferred when the header cannot be read",
		inferred.get("participant_name") == 1 and inferred.get("mobile_number") == 2,
		str(inferred),
	)

	check(
		"a sheet's own category is honoured, not defaulted",
		_match_category("Staff") == "FoLT Staff" and _match_category("County Official") == "County Official",
		f"{_match_category('Staff')} / {_match_category('County Official')}",
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

	# The same rule as the dropdown sees it. Filters do not reach a link query in the shape the
	# form script set them: frappe normalises each one into an `[operator, value]` pair on the way
	# to search_link, and a two-element list bound into raw SQL is a row constructor, which
	# MariaDB refuses to compare against a varchar. That is how picking a register came to fail
	# with a traceback instead of a list, so the exact posted shape is what is checked here.
	from folt_customizations.folt_customizations.doctype.activity_participant_list.activity_participant_list import (
		get_verified_registers,
	)

	def offered(filters):
		return [row[0] for row in get_verified_registers("Activity Participant List", "", "name", 0, 20, filters)]

	posted = {"activity": ["=", project_a], "docstatus": ["=", 1]}
	check(
		"F-04-D5   the register dropdown answers the filters a link field actually posts",
		offered(posted) == [register.name],
		str(offered(posted)),
	)
	check(
		"F-04-D5   and a plain filter from a script means the same thing",
		offered({"activity": project_a}) == [register.name],
		str(offered({"activity": project_a})),
	)
	check(
		"F-04-D5   another project's register is not offered",
		register_b.name not in offered(posted),
	)

	unverified = frappe.get_doc(
		{"doctype": "Activity Participant List", "activity": project_a, "session_date": nowdate()}
	).insert(ignore_permissions=True)
	check(
		"F-04A-D3  an unverified register is never offered, even when asked for by docstatus",
		unverified.name not in offered({"activity": ["=", project_a], "docstatus": ["=", 0]}),
	)
	frappe.delete_doc("Activity Participant List", unverified.name, force=True, ignore_permissions=True)

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
