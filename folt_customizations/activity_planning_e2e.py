"""End-to-end check of Finance SOP step 1: Activity Planning and Budget Approval.

The SOP, as written:

    A requisition is raised by the relevant program or admin staff. The Finance Department
    receives the approved Requisition, Budget and Activity Concept Note from the Program
    Department. Following review and approval by the Head of Finance and the Head of Programs,
    procurement and other financial preparations are undertaken based on the nature and
    requirements of the planned activity.

What this proves, each step taken by the person who takes it in real life:

  raised       staff (Employee) raise the requisition; it starts in Draft
  documents    it cannot leave Draft without the budget and the concept note attached, and the
               form says so before anyone presses the button
  Programs     the Head of Programs reviews first, may return it for revision or reject it (with
               a reason), cannot approve their own, and is recorded as the Programs approver
  Finance      only then does Finance receive it; the Head of Finance may return or reject it,
               cannot be the same person who approved for Programs, and approval submits it,
               records the Finance approver and opens the activity's Project
  preparation  procurement (a purchase requisition) and the cash float are raised only from an
               approved requisition and carry its budget coding; a rejected one starts nothing

Idempotent: tears down its own documents first and last. Run with:

    bench --site <site> execute folt_customizations.activity_planning_e2e.run
"""

import frappe
from frappe.model.workflow import apply_workflow
from frappe.utils import add_days, flt, nowdate

from folt_customizations import activity_chain
from folt_customizations.document_guide import get_guide
from folt_customizations.folt_customizations.page.folt_tasks.folt_tasks import my_tasks
from folt_customizations.workflow_access import REJECTION_REASON_FIELD, hold_rejection_reason

MARK = "E2E Planning"
ACTIVITY = f"{MARK} Community Water Governance Forum"
REJECTED_ACTIVITY = f"{MARK} Duplicate Forum"
BUDGET = 180000
FLOAT = 120000
BUDGET_LINE = "3.1.4"

REQUESTER = "requester.test@folt.test"
HEAD_OF_PROGRAMS = "hop.test@folt.test"
HEAD_OF_FINANCE = "hof.test@folt.test"
# One person holding both heads' roles -- an acting arrangement -- to prove two roles held by one
# person do not make two approvals. Kept between runs and disabled when the suite finishes.
DUAL_HEAD = "e2e.dualhead@folt.test"

USERS = (REQUESTER, HEAD_OF_PROGRAMS, HEAD_OF_FINANCE)

PASS, FAIL = [], []


# --- shared with the other suites that take a requisition out of Draft ---------------------------


def planning_documents(prefix: str = "e2e") -> dict:
	"""Real private File records for the two attachments SOP step 1 requires, as field values.

	Every suite that moves a requisition past Draft needs them now; made once per prefix and
	reused, so re-running a suite does not pile up files.
	"""
	values = {}
	for fieldname, label in (("concept_note", "concept-note"), ("budget_document", "activity-budget")):
		file_name = f"{prefix}-{label}.txt"
		url = frappe.db.get_value("File", {"file_name": file_name, "is_private": 1}, "file_url")
		if not url:
			url = (
				frappe.get_doc(
					{
						"doctype": "File",
						"file_name": file_name,
						"is_private": 1,
						"content": f"{label} for an end-to-end test requisition".encode(),
					}
				)
				.insert(ignore_permissions=True)
				.file_url
			)
		values[fieldname] = url
	return values


# --- harness -------------------------------------------------------------------------------------


def check(label, condition, detail=""):
	(PASS if condition else FAIL).append(label)
	print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")


def expect_throw(label, fn, exc=frappe.ValidationError, contains=None):
	try:
		fn()
	except exc as e:
		message = str(e).replace("\n", " ")
		ok = contains is None or all(part in message for part in contains)
		check(label, ok, message[:140])
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


def act(user, name, action, reason=None):
	def go():
		if reason:
			hold_rejection_reason("Activity Requisition", name, reason)
		return apply_workflow(frappe.get_doc("Activity Requisition", name), action)

	return as_user(user, go)


def state(name):
	return frappe.db.get_value("Activity Requisition", name, ["workflow_state", "docstatus"], as_dict=True)


def comments(name):
	return frappe.get_all(
		"Comment",
		filters={"reference_doctype": "Activity Requisition", "reference_name": name, "comment_type": "Workflow"},
		pluck="content",
	)


def employee_for(user):
	return frappe.db.get_value("Employee", {"user_id": user, "status": "Active"})


def require_users():
	missing = [user for user in USERS if not frappe.db.exists("User", user)]
	if missing:
		raise SystemExit(f"seed the role test users first (seed_test_users.py): missing {missing}")


def ensure_dual_head(enabled: bool):
	if not frappe.db.exists("User", DUAL_HEAD):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": DUAL_HEAD,
				"first_name": "E2E",
				"last_name": "Acting Head",
				"send_welcome_email": 0,
				"user_type": "System User",
			}
		)
		user.append("roles", {"role": "Head of Programs"})
		user.append("roles", {"role": "Head of Finance"})
		user.insert(ignore_permissions=True)
	frappe.db.set_value("User", DUAL_HEAD, "enabled", 1 if enabled else 0)


def teardown():
	# Only ever documents this suite made: an `in` filter on an empty list would match every
	# document whose link is blank, so nothing is queried by link until there is something to match.
	requisitions = frappe.get_all(
		"Activity Requisition", filters={"activity_program": ["like", f"{MARK}%"]}, pluck="name"
	)
	linked = (lambda doctype: frappe.get_all(
		doctype, filters={"folt_activity_requisition": ["in", requisitions]}, pluck="name"
	)) if requisitions else (lambda doctype: [])
	purchases = set(linked("Material Request")) | set(
		frappe.get_all("Material Request", filters={"title": ["like", f"{MARK}%"]}, pluck="name")
	)
	for name in purchases:
		doc = frappe.get_doc("Material Request", name)
		if doc.docstatus == 1:
			doc.cancel()
		frappe.delete_doc("Material Request", name, force=True, ignore_permissions=True)
	for name in linked("Employee Advance"):
		doc = frappe.get_doc("Employee Advance", name)
		if doc.docstatus == 1:
			doc.cancel()
		frappe.delete_doc("Employee Advance", name, force=True, ignore_permissions=True)
	for name in requisitions:
		doc = frappe.get_doc("Activity Requisition", name)
		if doc.docstatus == 1:
			doc.cancel()
		frappe.delete_doc("Activity Requisition", name, force=True, ignore_permissions=True)
	for name in frappe.get_all("Project", filters={"project_name": ["like", f"{MARK}%"]}, pluck="name"):
		frappe.delete_doc("Project", name, force=True, ignore_permissions=True)
	frappe.db.commit()


def raise_requisition(activity, attach=False):
	values = {
		"doctype": "Activity Requisition",
		"activity_program": activity,
		"requested_by": employee_for(REQUESTER),
		"activity_date": add_days(nowdate(), 21),
		"activity_end_date": add_days(nowdate(), 22),
		"venue": "E2E Lodwar Catholic Diocese Hall",
		"company": frappe.defaults.get_defaults().get("company") or frappe.get_all("Company", pluck="name")[0],
		"budget_amount": BUDGET,
		"budget_line": BUDGET_LINE,
		"float_required": 1,
		"float_amount": FLOAT,
		"description": "E2E: two-day forum for water user associations on borehole governance.",
	}
	if attach:
		values.update(planning_documents())
	return as_user(REQUESTER, lambda: frappe.get_doc(values).insert()).name


def attach(name, **fields):
	def go():
		doc = frappe.get_doc("Activity Requisition", name)
		doc.update(fields)
		doc.save()

	as_user(REQUESTER, go)


# --- the walk ------------------------------------------------------------------------------------


def run():
	frappe.set_user("Administrator")
	frappe.flags.mute_emails = True
	require_users()
	teardown()
	ensure_dual_head(True)
	frappe.db.commit()
	try:
		walk()
	finally:
		frappe.db.rollback()
		frappe.set_user("Administrator")
		teardown()
		ensure_dual_head(False)
		frappe.db.commit()

	print(f"\n{'=' * 60}\nRESULT: {len(PASS)} passed, {len(FAIL)} failed")
	if FAIL:
		for label in FAIL:
			print(f"  FAILED: {label}")
		raise SystemExit(1)


def walk():
	docs = planning_documents()

	print("\n--- raised by programme or admin staff ---")
	name = raise_requisition(ACTIVITY)
	row = state(name)
	check("staff holding Employee raise a requisition, in Draft", row.workflow_state == "Draft" and row.docstatus == 0, f"{name} {row.workflow_state}")
	check("it belongs to the person who raised it", frappe.db.get_value("Activity Requisition", name, "owner") == REQUESTER)

	print("\n--- the budget and the concept note travel with it ---")
	guide = as_user(REQUESTER, lambda: get_guide("Activity Requisition", name))
	check(
		"the form says up front that both documents block the next step",
		set(guide["blocked_by"]) == {"Concept Note Attachment", "Activity Budget"},
		str(guide["blocked_by"]),
	)
	expect_throw(
		"it cannot go for review with neither attached",
		lambda: act(REQUESTER, name, "Submit for Review"),
		contains=("Concept Note Attachment", "Activity Budget"),
	)
	attach(name, concept_note=docs["concept_note"])
	expect_throw(
		"nor with the concept note but no budget",
		lambda: act(REQUESTER, name, "Submit for Review"),
		contains=("Activity Budget",),
	)
	check("and it really stayed in Draft", state(name).workflow_state == "Draft")
	attach(name, budget_document=docs["budget_document"])
	guide = as_user(REQUESTER, lambda: get_guide("Activity Requisition", name))
	check("with both attached nothing blocks it", guide["blocked_by"] == [], str(guide["blocked_by"]))
	act(REQUESTER, name, "Submit for Review")
	check("submitted, it goes to the Head of Programs first", state(name).workflow_state == "Pending Head of Programs")

	print("\n--- the Head of Programs reviews ---")
	expect_throw(
		"the requester cannot approve it",
		lambda: act(REQUESTER, name, "Approve"),
		exc=Exception,
	)
	expect_throw(
		"the Head of Finance cannot approve it ahead of Programs",
		lambda: act(HEAD_OF_FINANCE, name, "Approve"),
		exc=Exception,
	)
	tasks = as_user(HEAD_OF_PROGRAMS, lambda: my_tasks("awaiting"))
	check(
		"it is in the Head of Programs' queue",
		any(r.get("name") == name for g in tasks["groups"] for r in g["rows"]),
	)
	expect_throw(
		"returning it for revision needs a reason",
		lambda: act(HEAD_OF_PROGRAMS, name, "Return for Revision"),
	)
	REVISE = "Split the venue hire from participant transport on the budget."
	act(HEAD_OF_PROGRAMS, name, "Return for Revision", REVISE)
	check("returned, it is back in Draft with the requester", state(name).workflow_state == "Draft")
	check("and the reason is in its timeline", any(REVISE in c for c in comments(name)), "; ".join(comments(name))[:120])
	attach(name, description="E2E: two-day forum. Venue hire KES 60,000; participant transport KES 120,000.")
	act(REQUESTER, name, "Submit for Review")
	check("the revised requisition comes back to Programs", state(name).workflow_state == "Pending Head of Programs")
	act(HEAD_OF_PROGRAMS, name, "Approve")
	doc = frappe.get_doc("Activity Requisition", name)
	check("approved for Programs, it goes to Finance", doc.workflow_state == "Pending Head of Finance" and doc.docstatus == 0)
	check(
		"the Programs approver and time are recorded",
		doc.programs_approved_by == HEAD_OF_PROGRAMS and bool(doc.programs_approved_on),
		f"{doc.programs_approved_by} {doc.programs_approved_on}",
	)
	check("the reason from the return does not ride forward", not doc.get(REJECTION_REASON_FIELD))

	print("\n--- Finance receives the requisition, budget and concept note ---")
	check(
		"Finance receives all three: the requisition with both documents on it",
		doc.concept_note == docs["concept_note"] and doc.budget_document == docs["budget_document"],
	)
	tasks = as_user(HEAD_OF_FINANCE, lambda: my_tasks("awaiting"))
	check(
		"it is in the Head of Finance's queue",
		any(r.get("name") == name for g in tasks["groups"] for r in g["rows"]),
	)
	guide = as_user(HEAD_OF_FINANCE, lambda: get_guide("Activity Requisition", name))
	check(
		"the form says it waits on the Head of Finance",
		"Head of Finance" in (guide["waiting_for"]["roles"] or []),
		str(guide["waiting_for"]["roles"]),
	)
	BACK = "Budget line 3.1.4 is fully committed; recode to 3.1.5."
	act(HEAD_OF_FINANCE, name, "Return for Revision", BACK)
	doc.reload()
	check("Finance can return it for revision too", doc.workflow_state == "Draft")
	check("which wipes the Programs approval: both heads approve the revised one", not doc.programs_approved_by and not doc.programs_approved_on)
	attach(name, budget_line="3.1.5")
	act(REQUESTER, name, "Submit for Review")
	act(HEAD_OF_PROGRAMS, name, "Approve")
	expect_throw(
		"Programs approval is not enough to start anything",
		lambda: as_user(REQUESTER, lambda: activity_chain.make_float_request(name)),
	)
	act(HEAD_OF_FINANCE, name, "Approve")
	doc.reload()
	check("Head of Finance approval completes it: Approved and submitted", doc.workflow_state == "Approved" and doc.docstatus == 1, f"{doc.workflow_state}/{doc.docstatus}")
	check(
		"both approvers are on it, Programs then Finance",
		doc.programs_approved_by == HEAD_OF_PROGRAMS
		and doc.finance_approved_by == HEAD_OF_FINANCE
		and doc.finance_approved_on >= doc.programs_approved_on,
	)
	check("approval opened the activity's Project", bool(doc.project) and frappe.db.get_value("Project", doc.project, "project_name") == ACTIVITY, str(doc.project))
	check("the revised budget line is what was approved", doc.budget_line == "3.1.5")

	print("\n--- approval is by two different people ---")
	# frappe's self-approval rule protects the document's owner, so the Head of Programs is made
	# the owner of one already up for their review (as if they had raised it themselves).
	own = raise_requisition(f"{MARK} Approver's Own", attach=True)
	act(REQUESTER, own, "Submit for Review")
	frappe.db.set_value("Activity Requisition", own, "owner", HEAD_OF_PROGRAMS)
	expect_throw(
		"the Head of Programs cannot approve a requisition they raised",
		lambda: act(HEAD_OF_PROGRAMS, own, "Approve"),
		exc=Exception,
	)
	acting = raise_requisition(f"{MARK} Acting Head", attach=True)
	act(REQUESTER, acting, "Submit for Review")
	act(DUAL_HEAD, acting, "Approve")
	expect_throw(
		"someone holding both roles cannot approve for Programs and then for Finance",
		lambda: act(DUAL_HEAD, acting, "Approve"),
		contains=("approved",),
	)
	check("so it waits for a second person", state(acting).workflow_state == "Pending Head of Finance")
	act(HEAD_OF_FINANCE, acting, "Approve")
	check("which the Head of Finance can be", state(acting).workflow_state == "Approved")

	print("\n--- turned down ---")
	rejected = raise_requisition(REJECTED_ACTIVITY, attach=True)
	act(REQUESTER, rejected, "Submit for Review")
	expect_throw("rejecting needs a reason", lambda: act(HEAD_OF_PROGRAMS, rejected, "Reject"))
	act(HEAD_OF_PROGRAMS, rejected, "Reject", "Duplicates the forum already approved for this quarter.")
	row = state(rejected)
	check("rejected, it is finished and never submitted", row.workflow_state == "Rejected" and row.docstatus == 0)
	expect_throw(
		"nothing can be raised from a rejected requisition",
		lambda: as_user(REQUESTER, lambda: activity_chain.make_float_request(rejected)),
	)

	print("\n--- then procurement and other financial preparations ---")
	status = as_user(REQUESTER, lambda: activity_chain.get_chain_status("Activity Requisition", name))
	ready = {h["label"]: h["ready"] for h in status["handoffs"]}
	check("the approved requisition offers the next documents", ready.get("Float Request") is True, str(ready))

	advance = frappe.get_doc("Employee Advance", as_user(REQUESTER, lambda: activity_chain.make_float_request(name)))
	check(
		"a cash float is raised from it, carrying the amount and approved budget line",
		flt(advance.advance_amount) == FLOAT and advance.folt_budget_line == "3.1.5" and advance.folt_activity_requisition == name,
		f"{advance.advance_amount} / {advance.folt_budget_line}",
	)

	item = frappe.db.get_value("Item", {"is_stock_item": 0, "disabled": 0}, "name")
	company = doc.company

	def purchase(activity_requisition):
		return frappe.get_doc(
			{
				"doctype": "Material Request",
				"material_request_type": "Purchase",
				"company": company,
				"title": f"{MARK} forum supplies",
				"transaction_date": nowdate(),
				"schedule_date": add_days(nowdate(), 14),
				"folt_justification": "E2E: supplies for the approved activity.",
				"folt_activity_requisition": activity_requisition,
				"items": [
					{
						"item_code": item,
						"qty": 2,
						"uom": frappe.db.get_value("Item", item, "stock_uom") or "Nos",
						"conversion_factor": 1,
						"schedule_date": add_days(nowdate(), 14),
						"folt_estimated_rate": 5000,
					}
				],
			}
		).insert()

	if not item:
		check("a non-stock item exists to buy", False, "no Item with is_stock_item=0")
		return
	expect_throw(
		"a purchase cannot be raised against a rejected requisition",
		lambda: as_user(REQUESTER, lambda: purchase(rejected)),
	)
	mr = as_user(REQUESTER, lambda: purchase(name))
	check("a purchase requisition is raised against the approved activity", mr.folt_activity_requisition == name and mr.workflow_state == "Draft", mr.name)
	check(
		"and it is charged to the activity's project",
		all(r.project == doc.project for r in mr.items),
		str([r.get("project") for r in mr.items]),
	)
