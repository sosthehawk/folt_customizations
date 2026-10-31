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
		for dt in ("FoLT Program", "FoLT Program Category", "FoLT Program Type"):
			title = masterdata.MASTERS[dt][2]
			for name in frappe.get_all(dt, filters={title: ["like", f"{MARK}%"]}, pluck="name"):
				frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
	finally:
		frappe.flags.in_test_teardown = False
	frappe.db.commit()


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

	print("\n--- the auditor reads and changes nothing ---")
	check("the auditor reads the dashboard", as_user(AUDITOR, pp.dashboard)["masters"])
	expect_throw("the auditor cannot create a category", lambda: as_user(AUDITOR, lambda: pp.save("FoLT Program Category", {"category_name": f"{MARK} Audit"})), frappe.PermissionError)

	dash = as_user(PREPARER, pp.dashboard)
	check("the dashboard counts locations by state", any(m["doctype"] == "FoLT Location" and m["by_state"].get("Active", 0) >= 83 for m in dash["masters"]))

	teardown()
	print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
	if FAIL:
		raise AssertionError(f"{len(FAIL)} checks failed: {FAIL}")
