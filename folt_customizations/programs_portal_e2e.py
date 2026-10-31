"""End-to-end check of Programs master data: the lifecycle (masterdata) and its edge (programs_portal).

Five claims. Only the programme, finance and audit roles get in, and the allow-list holds. A record
people create starts in Draft, is approved by somebody else, and only then can be linked; a
turn-down needs a reason. A record that has been in use is deactivated, never deleted. Codes are
generated and unique, and a name that is an existing one in different clothes is refused. And the
location tree holds Kenya's 47 counties with FoLT's Turkana places beneath their sub-counties.

Idempotent: tears down its own records first. Run with

    bench --site <site> execute folt_customizations.programs_portal_e2e.run
"""

import frappe

from folt_customizations import masterdata
from folt_customizations import programs_portal as pp

MARK = "E2E MD"

PREPARER = "progofficer.test@folt.test"  # Program Officer
APPROVER = "mdapprover.test@folt.test"  # Master Data Approver
ADMIN = "mdadmin.test@folt.test"  # Master Data Administrator
BOTH = "hop.test@folt.test"  # Head of Programs: prepares and approves
AUDITOR = "ed.test@folt.test"  # Executive Director + Auditor
OUTSIDER = "requester.test@folt.test"  # Employee only

PASS, FAIL = [], []


def check(label, condition, detail=""):
	(PASS if condition else FAIL).append(label)
	print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  — ' + str(detail) if detail else ''}")


def expect_throw(label, fn, exc=Exception):
	try:
		fn()
	except exc as e:
		check(label, True, frappe.utils.strip_html(str(e)).strip()[:90].replace("\n", " ") or type(e).__name__)
		frappe.db.rollback()
		return
	except Exception as e:  # noqa: BLE001
		check(label, False, f"wrong error type: {type(e).__name__}: {e}")
		frappe.db.rollback()
		return
	check(label, False, "no error raised")


def as_user(user, fn):
	previous = frappe.session.user
	frappe.set_user(user)
	try:
		return fn()
	finally:
		frappe.set_user(previous)


def teardown():
	frappe.set_user("Administrator")
	frappe.flags.in_test_teardown = True
	try:
		for name in frappe.get_all("Data Import", filters={"reference_doctype": "FoLT Program Category", "owner": ["in", [ADMIN, BOTH]]}, pluck="name"):
			frappe.delete_doc("Data Import", name, force=True, ignore_permissions=True)
		for name in frappe.get_all("Activity Requisition", filters={"activity_program": ["like", f"{MARK}%"]}, pluck="name"):
			frappe.delete_doc("Activity Requisition", name, force=True, ignore_permissions=True)
		# Generated periods are named by their year, not by MARK: found by their test donor.
		for name in frappe.get_all("FoLT Reporting Period", filters={"donor": ["like", f"{MARK}%"]}, pluck="name"):
			frappe.delete_doc("FoLT Reporting Period", name, force=True, ignore_permissions=True)
		# Dependants first: what links to a master goes before it.
		for dt in ("FoLT Funding Allocation", "FoLT Reporting Period", "FoLT Program", "FoLT Activity Template", "FoLT Indicator",
		           "FoLT Output", "FoLT Outcome", "FoLT Objective", "FoLT Grant Project", "FoLT Donor", "FoLT Partner",
		           "FoLT Beneficiary Category", "FoLT Budget Line", "FoLT Program Category", "FoLT Program Type"):
			title = masterdata.MASTERS[dt][2]
			for name in frappe.get_all(dt, filters={title: ["like", f"{MARK}%"]}, pluck="name", order_by="creation desc"):
				frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
	finally:
		frappe.flags.in_test_teardown = False
	frappe.db.commit()


ME = "meofficer.test@folt.test"  # M&E Officer
MANAGER = "progmanager.test@folt.test"  # Program Manager


def active(user, doctype, values):
	"""A record prepared by `user` and approved by the approver: what other records may link to."""
	name = as_user(user, lambda: pp.save(doctype, values))["name"]
	as_user(user, lambda: pp.act(doctype, name, "transition", "Submit for Review"))
	as_user(APPROVER, lambda: pp.act(doctype, name, "transition", "Approve"))
	frappe.db.commit()
	return name


def phases_4_to_6():
	print("\n--- funders and partners: what is sensitive stays with those who manage funders ---")
	donor = active(MANAGER, "FoLT Donor", {"donor_name": f"{MARK} Lake Fund", "organization_type": "Foundation", "email": "grants@lakefund.example",
	                                       "funding_conditions": "Spend within the lake basin."})
	seen = as_user(PREPARER, lambda: pp.record("FoLT Donor", donor))["doc"]
	check("a programme officer does not see a donor's email or conditions", not seen.get("email") and not seen.get("funding_conditions"), sorted(k for k, v in seen.items() if v))
	check("a programme manager does", as_user(MANAGER, lambda: pp.record("FoLT Donor", donor))["doc"].get("email") == "grants@lakefund.example")
	expect_throw("a partner named like an existing one is refused", lambda: (
		as_user(PREPARER, lambda: pp.save("FoLT Partner", {"partner_name": f"{MARK} Turkana Women Network"})),
		frappe.db.commit(),
		as_user(PREPARER, lambda: pp.save("FoLT Partner", {"partner_name": f"{MARK} turkana women network ltd"})),
	))

	print("\n--- the results framework: each level hangs from an approved one ---")
	objective = active(ME, "FoLT Objective", {"objective_name": f"{MARK} Children learn"})
	check("an objective is named by its generated code", objective.startswith("OBJ-"), objective)
	expect_throw("an outcome cannot hang from a draft objective", lambda: as_user(ME, lambda: (
		pp.save("FoLT Outcome", {"outcome_name": f"{MARK} orphan", "objective": as_user(ME, lambda: pp.save("FoLT Objective", {"objective_name": f"{MARK} Draft objective"}))["name"]}))))
	outcome = active(ME, "FoLT Outcome", {"outcome_name": f"{MARK} More children enrol", "objective": objective})
	output = active(ME, "FoLT Output", {"output_name": f"{MARK} Classrooms built", "outcome": outcome, "expected_quantity": 4, "uom": "Nos"})
	indicator = active(ME, "FoLT Indicator", {"indicator_name": f"{MARK} Classrooms in use", "indicator_type": "Quantitative", "output": output,
	                                         "baseline_value": 0, "target_value": 4, "frequency": "Quarterly"})
	activity = active(MANAGER, "FoLT Activity Template", {"activity_name": f"{MARK} School construction", "objective": objective, "outcome": outcome,
	                                                     "expected_output": output, "indicators": [{"indicator": indicator}], "locations": [{"location": "Kakuma"}]})
	program = active(MANAGER, "FoLT Program", {"program_name": f"{MARK} Lake schools", "objectives": [{"objective": objective}],
	                                          "activities": [{"activity_template": activity}], "locations": [{"location": "Turkana West"}]})
	shown = as_user(MANAGER, lambda: pp.record("FoLT Program", program))
	check("a programme names its objective and activity by title", shown["link_titles"].get("FoLT Objective", {}).get(objective) == f"{MARK} Children learn"
	      and shown["link_titles"].get("FoLT Activity Template", {}).get(activity) == f"{MARK} School construction", shown["link_titles"])
	check("an outcome list gives its objectives' titles", as_user(ME, lambda: pp.records("FoLT Outcome", q=MARK))["link_titles"].get("FoLT Objective", {}).get(objective) == f"{MARK} Children learn")
	used = as_user(ME, lambda: pp.record("FoLT Indicator", indicator))["usage"]
	check("an indicator shows the activity measured by it", any(u["doctype"] == "FoLT Activity Template" and activity in u["sample"] for u in used), used)
	check("an objective shows its outcome and the programme", {"FoLT Outcome", "FoLT Program"} <= {u["doctype"] for u in as_user(ME, lambda: pp.record("FoLT Objective", objective))["usage"]})

	print("\n--- money: allocations stay within what a funding source holds ---")
	source = active(MANAGER, "FoLT Grant Project", {"grant_name": f"{MARK} Lake Fund schools grant", "donor": donor, "grant_currency": "KES", "total_amount": 1000000})
	first = active(MANAGER, "FoLT Funding Allocation", {"allocation_title": f"{MARK} Schools 2026", "program": program, "funding_source": source, "amount": 700000})
	check("an allocation takes its donor and currency from the source", frappe.db.get_value("FoLT Funding Allocation", first, ["donor", "currency"]) == (donor, "KES"))
	expect_throw("a second allocation beyond the source's total is refused", lambda: as_user(MANAGER, lambda: pp.save("FoLT Funding Allocation", {
		"allocation_title": f"{MARK} Too much", "program": program, "funding_source": source, "amount": 400000})))
	line = active(MANAGER, "FoLT Budget Line", {"budget_line_name": f"{MARK} Construction", "classification": "Budget line"})
	check("a budget line is named by its code", line.startswith("BGL-"), line)

	print("\n--- reporting periods ---")
	expect_throw("a report cannot be due before its period ends", lambda: as_user(MANAGER, lambda: pp.save("FoLT Reporting Period", {
		"period_name": f"{MARK} Q1 bad", "frequency": "Quarterly", "start_date": "2026-07-01", "end_date": "2026-09-30", "deadline": "2026-09-01"})))
	late = active(MANAGER, "FoLT Reporting Period", {"period_name": f"{MARK} Q4 2025", "frequency": "Quarterly", "program": program, "donor": donor,
	                                                  "start_date": "2025-10-01", "end_date": "2025-12-31", "deadline": "2026-01-31"})
	check("an open report past its due date is Overdue", frappe.db.get_value("FoLT Reporting Period", late, "reporting_status") == "Overdue")
	masterdata.flag_reporting_periods()

	print("\n--- the activity chain names approved master data only ---")
	requester = frappe.db.get_value("Employee", {"user_id": "requester.test@folt.test"}, "name")
	draft_program = as_user(MANAGER, lambda: pp.save("FoLT Program", {"program_name": f"{MARK} Not yet approved"}))["name"]
	frappe.db.commit()
	def requisition(prog):
		return frappe.get_doc({"doctype": "Activity Requisition", "activity_program": f"{MARK} activity", "requested_by": requester,
		                       "activity_date": "2026-11-20", "company": frappe.defaults.get_global_default("company") or frappe.get_all("Company", pluck="name")[0],
		                       "budget_amount": 1000, "budget_line": "Transport", "program": prog, "budget_classification": line}).insert()
	expect_throw("a requisition cannot name a programme still in draft", lambda: as_user("requester.test@folt.test", lambda: requisition(draft_program)))
	req = as_user("requester.test@folt.test", lambda: requisition(program))
	frappe.db.commit()
	check("a requisition names an approved programme and budget line", frappe.db.get_value("Activity Requisition", req.name, ["program", "budget_classification"]) == (program, line))


def phases_7_and_8():
	import time

	print("\n--- approvals: what awaits whom, and how each lifecycle is set ---")
	pending = as_user(PREPARER, lambda: pp.save("FoLT Program Type", {"type_name": f"{MARK} Community-based"}))["name"]
	as_user(PREPARER, lambda: pp.act("FoLT Program Type", pending, "transition", "Submit for Review"))
	frappe.db.commit()
	queue = as_user(APPROVER, pp.approvals)
	mine = [w for w in queue["waiting"] if w["name"] == pending]
	check("the approver's queue holds it, with Approve, Return and Reject", mine and {m["action"] for m in mine[0]["moves"]} == {"Approve", "Return for Revision", "Reject"}, mine)
	check("the preparer sees it among what they sent", any(r["name"] == pending for r in as_user(PREPARER, pp.approvals)["mine"]))
	check("the preparer has nothing of it to decide", not any(w["name"] == pending for w in as_user(PREPARER, pp.approvals)["waiting"]))
	life = next(l for l in queue["lifecycles"] if l["doctype"] == "FoLT Program Type")
	approve = next(t for t in life["transitions"] if t["action"] == "Approve")
	check("a lifecycle says who approves, and that nobody approves their own", "Master Data Approver" in approve["roles"] and not approve["self_approval"], approve)
	as_user(APPROVER, lambda: pp.act("FoLT Program Type", pending, "transition", "Approve"))
	frappe.db.commit()

	print("\n--- the audit trail ---")
	trail = as_user(AUDITOR, lambda: pp.audit(doctype="FoLT Program Type"))["events"]
	check("the auditor reads the type's changes and moves", any(e["name"] == pending and e["kind"] == "workflow" for e in trail)
	      and any(e["name"] == pending and e["kind"] == "change" for e in trail), [(e["kind"], e["detail"]) for e in trail if e["name"] == pending][:4])

	print("\n--- import, through Frappe's Data Import ---")
	template = as_user(ADMIN, lambda: (pp.import_template("FoLT Program Category"), frappe.response.filecontent)[1])
	check("the template is the fields a person fills in", template.strip().split(",")[:2] == ["Category", "Code"], template.strip())
	rows = f"Category,Description\n{MARK} Imported Health,Clinics\n{MARK} Imported Water,Boreholes\n{MARK} imported health ltd,Repeats the first\n"

	def upload():
		di = frappe.get_doc({"doctype": "Data Import", "reference_doctype": "FoLT Program Category", "import_type": "Insert New Records"}).insert()
		f = frappe.get_doc({"doctype": "File", "file_name": "categories.csv", "content": rows, "attached_to_doctype": "Data Import",
		                    "attached_to_name": di.name, "attached_to_field": "import_file", "is_private": 1}).insert()
		di.import_file = f.file_url
		di.save()
		return pp._preview(di)

	preview = as_user(ADMIN, upload)
	frappe.db.commit()
	check("the preview reads three rows", preview["total"] == 3, preview["total"])
	check("the preview names the row that repeats another", any("Row 4" in d and "repeats row 2" in d for d in preview["duplicates"]), preview["duplicates"])
	as_user(ADMIN, lambda: pp.import_run(preview["name"]))
	frappe.db.commit()
	status = {}
	for _ in range(60):
		time.sleep(1)
		frappe.db.rollback()  # a fresh snapshot: the import commits in a worker
		status = as_user(ADMIN, lambda: pp.import_status(preview["name"]))
		if status["status"] in ("Success", "Partial Success", "Error"):
			break
	check("two rows import and the repeat is refused", status["imported"] == 2 and status["failed"] == 1, {k: status.get(k) for k in ("status", "imported", "failed")})
	check("the refusal says why", any("looks like" in m for f in status["failures"] for m in f["messages"]), status["failures"])
	imported = frappe.db.get_value("FoLT Program Category", f"{MARK} Imported Water", ["workflow_state", "category_code"])
	check("an imported record arrives in Draft, with a code", imported and imported[0] == masterdata.DRAFT and imported[1].startswith("CAT-"), imported)
	expect_throw("a programme officer cannot import", lambda: as_user(PREPARER, lambda: pp.import_template("FoLT Program Category")), frappe.PermissionError)

	print("\n--- a year of reporting periods ---")
	fy = frappe.get_all("Fiscal Year", pluck="name", order_by="year_start_date desc")[0]
	made = as_user(MANAGER, lambda: pp.generate_periods(fy, "Quarterly", donor=frappe.db.get_value("FoLT Donor", {"donor_name": f"{MARK} Lake Fund"}), deadline_days=30))
	frappe.db.commit()
	check("a quarterly year is four draft periods", len(made["created"]) == 4 and all(frappe.db.get_value("FoLT Reporting Period", n, "workflow_state") == masterdata.DRAFT for n in made["created"]), made)
	again = as_user(MANAGER, lambda: pp.generate_periods(fy, "Quarterly", donor=frappe.db.get_value("FoLT Donor", {"donor_name": f"{MARK} Lake Fund"}), deadline_days=30))
	check("generating again makes none twice", not again["created"] and len(again["kept"]) == 4, again)


def run():
	teardown()

	print("\n--- only the programme, finance and audit roles get in ---")
	expect_throw("an employee without a programme role is refused", lambda: as_user(OUTSIDER, pp.context), frappe.PermissionError)
	ctx = as_user(PREPARER, pp.context)
	check("a programme officer may create categories", ctx["capabilities"]["FoLT Program Category"]["create"])
	check("a programme officer is a preparer, not an approver", ctx["roles"]["preparer"] and not ctx["roles"]["approver"])
	check("Department is shown, not written", ctx["capabilities"]["Department"]["read"] and not ctx["capabilities"]["Department"]["write"])
	expect_throw("a doctype off the allow-list is refused", lambda: as_user(PREPARER, lambda: pp.records("Salary Slip")), frappe.PermissionError)
	expect_throw("a link search off the allow-list is refused", lambda: as_user(PREPARER, lambda: pp.link_search("Salary Slip", "")), frappe.PermissionError)
	expect_throw("Department cannot be saved from here", lambda: as_user(ADMIN, lambda: pp.save("Department", {"department_name": f"{MARK} dept"})), frappe.PermissionError)

	print("\n--- people's records start in Draft and need somebody else's approval ---")
	saved = as_user(PREPARER, lambda: pp.save("FoLT Program Category", {"category_name": f"{MARK} Education"}))
	frappe.db.commit()
	cat = frappe.get_doc("FoLT Program Category", saved["name"])
	check("a new category starts in Draft, inactive", cat.workflow_state == masterdata.DRAFT and not cat.is_active, cat.workflow_state)
	check("a code is generated from the prefix", (cat.category_code or "").startswith("CAT-"), cat.category_code)
	found = as_user(PREPARER, lambda: pp.link_search("FoLT Program Category", MARK))
	check("a draft is not offered by the link search", not any(o.get("value") == cat.name for o in found))
	expect_throw(
		"a programme cannot link a draft category",
		lambda: as_user(PREPARER, lambda: pp.save("FoLT Program", {"program_name": f"{MARK} Programme", "category": cat.name})),
	)

	rec = as_user(PREPARER, lambda: pp.record("FoLT Program Category", cat.name))
	check("the preparer is offered Submit for Review", [t["action"] for t in rec["workflow"]["transitions"]] == ["Submit for Review"], rec["workflow"]["transitions"])
	as_user(PREPARER, lambda: pp.act("FoLT Program Category", cat.name, "transition", "Submit for Review"))
	frappe.db.commit()
	check("submitted: Pending Review", frappe.db.get_value("FoLT Program Category", cat.name, "workflow_state") == masterdata.PENDING)

	moves = {t["action"]: t for t in as_user(APPROVER, lambda: pp.record("FoLT Program Category", cat.name))["workflow"]["transitions"]}
	check("the approver may approve, return or reject", {"Approve", "Return for Revision", "Reject"} <= set(moves), sorted(moves))
	check("Return for Revision and Reject need a reason", moves["Return for Revision"]["needs_reason"] and moves["Reject"]["needs_reason"] and not moves["Approve"]["needs_reason"])
	expect_throw("returning without a reason is refused", lambda: as_user(APPROVER, lambda: pp.act("FoLT Program Category", cat.name, "transition", "Return for Revision")))
	as_user(APPROVER, lambda: pp.act("FoLT Program Category", cat.name, "transition", "Return for Revision", reason="Say which sector this covers."))
	frappe.db.commit()
	cat.reload()
	check("returned with a reason: back in Draft, reason kept", cat.workflow_state == masterdata.DRAFT and "sector" in (cat.folt_rejection_reason or ""), cat.folt_rejection_reason)

	as_user(PREPARER, lambda: pp.save("FoLT Program Category", {"description": "Schools and learning", "modified": str(cat.modified)}, cat.name))
	as_user(PREPARER, lambda: pp.act("FoLT Program Category", cat.name, "transition", "Submit for Review"))
	frappe.db.commit()

	# A preparer who also holds the approver role (Head of Programs) cannot approve their own record.
	mine = as_user(BOTH, lambda: pp.save("FoLT Program Type", {"type_name": f"{MARK} Pilot"}))
	frappe.db.commit()
	frappe.set_user("Administrator")
	frappe.get_doc("FoLT Program Type", mine["name"]).db_set("workflow_state", masterdata.PENDING)
	frappe.db.commit()
	own = [t["action"] for t in as_user(BOTH, lambda: pp.record("FoLT Program Type", mine["name"]))["workflow"]["transitions"]]
	check("an approver is not offered Approve on their own record", "Approve" not in own, own)
	expect_throw("and approving it anyway is refused", lambda: as_user(BOTH, lambda: pp.act("FoLT Program Type", mine["name"], "transition", "Approve")))

	as_user(APPROVER, lambda: pp.act("FoLT Program Category", cat.name, "transition", "Approve"))
	frappe.db.commit()
	cat.reload()
	check("approved: Active and active", cat.workflow_state == masterdata.ACTIVE and cat.is_active)
	check("a preparer is not offered Edit on an Active record", not as_user(PREPARER, lambda: pp.record("FoLT Program Category", cat.name))["permissions"]["write"])
	check("its steward is", as_user(ADMIN, lambda: pp.record("FoLT Program Category", cat.name))["permissions"]["write"])
	history = as_user(PREPARER, lambda: pp.record("FoLT Program Category", cat.name))["timeline"]["versions"]
	check("the history leaves out is_active and the tree's own fields", not any("is_active" in v["detail"] for v in history), [v["detail"] for v in history])
	found = as_user(PREPARER, lambda: pp.link_search("FoLT Program Category", MARK))
	check("an active category is offered by the link search", any(o.get("value") == cat.name for o in found), found)

	prog = as_user(PREPARER, lambda: pp.save("FoLT Program", {"program_name": f"{MARK} Programme", "category": cat.name,
	                                                            "locations": [{"location": "Kakuma"}, {"location": "Turkana County"}]}))
	frappe.db.commit()
	check("a programme links the active category and two locations", frappe.db.get_value("FoLT Program", prog["name"], "category") == cat.name)
	listed = as_user(PREPARER, lambda: pp.records("FoLT Program", filters=[["locations", "has", "Kakuma"]]))
	check("the programme list filters by location", any(r["name"] == prog["name"] for r in listed["rows"]), listed["total"])
	usage = as_user(PREPARER, lambda: pp.record("FoLT Program Category", cat.name))["usage"]
	check("the category shows where it is used", any(u["doctype"] == "FoLT Program" and prog["name"] in u["sample"] for u in usage), usage)

	print("\n--- in use: deactivated, never deleted ---")
	expect_throw("the preparer cannot deactivate", lambda: as_user(PREPARER, lambda: pp.act("FoLT Program Category", cat.name, "transition", "Deactivate")))
	expect_throw("an active category cannot be deleted", lambda: as_user(ADMIN, lambda: pp.act("FoLT Program Category", cat.name, "delete")))
	as_user(ADMIN, lambda: pp.act("FoLT Program Category", cat.name, "transition", "Deactivate"))
	frappe.db.commit()
	check("deactivated: Inactive, no longer active", frappe.db.get_value("FoLT Program Category", cat.name, ["workflow_state", "is_active"]) == (masterdata.INACTIVE, 0))
	check("the programme keeps its category", frappe.db.get_value("FoLT Program", prog["name"], "category") == cat.name)
	expect_throw("an inactive category cannot be deleted either", lambda: as_user(ADMIN, lambda: pp.act("FoLT Program Category", cat.name, "delete")))

	print("\n--- codes and duplicates ---")
	expect_throw(
		"a category named like an existing one is refused",
		lambda: as_user(PREPARER, lambda: pp.save("FoLT Program Category", {"category_name": f"{MARK} education ltd."})),
	)
	expect_throw(
		"a code already used is refused",
		lambda: as_user(PREPARER, lambda: pp.save("FoLT Program Category", {"category_name": f"{MARK} Health", "category_code": cat.category_code})),
	)
	draft = as_user(PREPARER, lambda: pp.save("FoLT Program Category", {"category_name": f"{MARK} Health"}))
	frappe.db.commit()
	as_user(ADMIN, lambda: pp.act("FoLT Program Category", draft["name"], "delete"))
	frappe.db.commit()
	check("a draft never approved can be deleted", not frappe.db.exists("FoLT Program Category", draft["name"]))

	print("\n--- the location tree ---")
	check("47 counties", frappe.db.count("FoLT Location", {"location_level": "County"}) == 47)
	check("every county is under Kenya", frappe.db.count("FoLT Location", {"location_level": "County", "parent_folt_location": "Kenya"}) == 47)
	check("Kakuma sits under Turkana West, under Turkana County",
	      frappe.db.get_value("FoLT Location", "Kakuma", "parent_folt_location") == "Turkana West"
	      and frappe.db.get_value("FoLT Location", "Turkana West", "parent_folt_location") == "Turkana County")
	roots = as_user(PREPARER, lambda: pp.tree("FoLT Location"))["nodes"]
	check("the tree's root is Kenya alone", [n["name"] for n in roots] == ["Kenya"], [n["name"] for n in roots])
	turkana = as_user(PREPARER, lambda: pp.tree("FoLT Location", "Turkana County"))["nodes"]
	check("Turkana County has its six sub-counties", len(turkana) == 6, [n["name"] for n in turkana])
	kakuma = as_user(PREPARER, lambda: pp.record("FoLT Location", "Kakuma"))["tree"]
	check("a location knows its ancestors", kakuma["ancestors"] == ["Kenya", "Turkana County", "Turkana West"], kakuma["ancestors"])

	phases_4_to_6()
	phases_7_and_8()

	print("\n--- the auditor reads and changes nothing ---")
	check("the auditor reads the dashboard", as_user(AUDITOR, pp.dashboard)["masters"])
	expect_throw("the auditor cannot create a category", lambda: as_user(AUDITOR, lambda: pp.save("FoLT Program Category", {"category_name": f"{MARK} Audit"})), frappe.PermissionError)

	dash = as_user(PREPARER, pp.dashboard)
	check("the dashboard counts locations by state", any(m["doctype"] == "FoLT Location" and m["by_state"].get("Active", 0) >= 83 for m in dash["masters"]))

	teardown()
	print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
	if FAIL:
		raise AssertionError(f"{len(FAIL)} checks failed: {FAIL}")
