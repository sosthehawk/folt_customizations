"""End-to-end check that a ledger voucher posts only through its approval.

Three claims. The workflow does what it says: the preparer submits for approval, an Accounts
Manager who did not prepare it approves and that posts the voucher, a rejection needs a reason
and goes back to Draft. The doors around it are shut: the REST API, `frappe.client.submit`,
`run_doc_method` and the list view's bulk submit all refuse to post a draft, for the approver as
much as for the preparer. And the vouchers ERPNext generates inside some other request -- the
payroll accrual a posted run makes is the one FoLT relies on -- still post.

Each door is simulated by setting the request path and `cmd` the way Frappe's router would.

Idempotent: tears down its own vouchers first. Run with

    bench --site <site> execute folt_customizations.finance_approvals_e2e.run
"""

from contextlib import contextmanager

import frappe
from frappe.model.workflow import apply_workflow

from folt_customizations.finance_approvals import submit_cancel_or_update_docs
from folt_customizations.workflow_access import REJECTION_REASON_FIELD, hold_rejection_reason

COMPANY = "FRIENDS of LAKE TURKANA"
MARK = "E2E finance approvals"

PREPARER = "finassistant.test@folt.test"  # Finance Assistant + Accounts User
APPROVER = "hof.test@folt.test"  # Head of Finance + Accounts Manager
SECOND_APPROVER = "finmanager.test@folt.test"  # Finance Manager + Accounts Manager

PASS, FAIL = [], []


def check(label, condition, detail=""):
	(PASS if condition else FAIL).append(label)
	print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")


def expect_throw(label, fn, exc=frappe.ValidationError):
	try:
		fn()
	except exc as e:
		check(label, True, frappe.utils.strip_html(str(e)).strip()[:90].replace("\n", " "))
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


@contextmanager
def request(path, **form):
	"""Pretend to be inside a web request to `path` with this form, as the router would set it."""
	saved_request, saved_form = getattr(frappe.local, "request", None), frappe.local.form_dict
	frappe.local.request = frappe._dict(path=path)
	frappe.local.form_dict = frappe._dict(form)
	try:
		yield
	finally:
		frappe.local.request, frappe.local.form_dict = saved_request, saved_form


def method(cmd, **form):
	return request(f"/api/method/{cmd}", cmd=cmd, **form)


def teardown():
	for name in frappe.get_all("Journal Entry", filters={"user_remark": MARK}, pluck="name", order_by="creation desc"):
		doc = frappe.get_doc("Journal Entry", name)
		if doc.docstatus == 1:
			doc.flags.ignore_permissions = True
			doc.cancel()
		frappe.delete_doc("Journal Entry", name, force=True, ignore_permissions=True)
	frappe.db.commit()


def make_voucher(amount=1000):
	expense = frappe.db.get_value("Account", {"company": COMPANY, "account_name": "Travel Expenses", "is_group": 0})
	cash = frappe.db.get_value("Company", COMPANY, "default_cash_account")
	cost_center = frappe.db.get_value("Company", COMPANY, "cost_center")
	doc = frappe.get_doc(
		{
			"doctype": "Journal Entry",
			"voucher_type": "Journal Entry",
			"company": COMPANY,
			"posting_date": frappe.utils.nowdate(),
			"user_remark": MARK,
			"accounts": [
				{"account": expense, "debit_in_account_currency": amount, "cost_center": cost_center},
				{"account": cash, "credit_in_account_currency": amount, "cost_center": cost_center},
			],
		}
	)
	doc.insert()
	return doc


def fresh(doc):
	return frappe.get_doc(doc.doctype, doc.name)


def run():
	frappe.set_user("Administrator")
	teardown()
	missing = [u for u in (PREPARER, APPROVER, SECOND_APPROVER) if not frappe.db.exists("User", u)]
	if missing:
		print(f"ABORT: run seed_test_users.py first, missing {missing}")
		return {"passed": 0, "failed": 1, "failures": ["seed users missing"]}

	print("\n--- the three workflows are there and active ---")
	for doctype in ("Journal Entry", "Payment Entry", "Purchase Invoice"):
		name = frappe.db.get_value("Workflow", {"document_type": doctype, "is_active": 1})
		check(f"{doctype} has an active approval workflow", bool(name), name or "")
		check(
			f"{doctype} carries the rejection reason field",
			frappe.get_meta(doctype).has_field(REJECTION_REASON_FIELD),
		)

	print("\n--- a draft cannot be posted around the approval ---")
	draft = as_user(PREPARER, make_voucher)
	check("the preparer saves a draft voucher", draft.docstatus == 0 and draft.workflow_state == "Draft", draft.name)

	for label, door in (
		("frappe.client.submit", lambda: method("frappe.client.submit")),
		("the REST API", lambda: request(f"/api/resource/Journal Entry/{draft.name}")),
		("the v2 document API", lambda: request(f"/api/v2/document/Journal Entry/{draft.name}/method/submit")),
		("run_doc_method on the voucher", lambda: method("run_doc_method", dt="Journal Entry", dn=draft.name, method="submit")),
		("savedocs (the Desk's Submit)", lambda: method("frappe.desk.form.save.savedocs")),
	):
		for user in (PREPARER,):

			def attempt(door=door):
				with door():
					fresh(draft).submit()

			expect_throw(f"{label} refuses to post the draft for {user.split('.')[0]}", lambda: as_user(user, attempt), frappe.PermissionError)

	own_draft = as_user(APPROVER, make_voucher)
	check("an Accounts Manager may prepare and edit a draft too", as_user(APPROVER, lambda: fresh(own_draft).save()) is not None)

	def approver_posts_own():
		with method("frappe.client.submit"):
			fresh(own_draft).submit()

	expect_throw("an approver cannot post their own draft directly either", lambda: as_user(APPROVER, approver_posts_own), frappe.PermissionError)
	check("the draft is still a draft", frappe.db.get_value("Journal Entry", draft.name, "docstatus") == 0)

	def bulk():
		with method("frappe.desk.doctype.bulk_update.bulk_update.submit_cancel_or_update_docs"):
			submit_cancel_or_update_docs("Journal Entry", [draft.name], action="submit")

	expect_throw("bulk submit refuses an approval-governed voucher", lambda: as_user(APPROVER, bulk), frappe.PermissionError)

	print("\n--- the approval posts it ---")
	as_user(PREPARER, lambda: apply_workflow(fresh(draft), "Submit for approval"))
	check("submit for approval moves it to Pending Approval", fresh(draft).workflow_state == "Pending Approval")

	expect_throw(
		"the preparer cannot approve",
		lambda: as_user(PREPARER, lambda: apply_workflow(fresh(draft), "Approve")),
		frappe.ValidationError,
	)

	def reject_without_reason():
		with method("frappe.model.workflow.apply_workflow"):
			apply_workflow(fresh(draft), "Reject")

	expect_throw("a rejection without a reason is refused", lambda: as_user(APPROVER, reject_without_reason))

	def reject_with_reason():
		doc = fresh(draft)
		hold_rejection_reason(doc.doctype, doc.name, "Wrong cost centre")
		with method("frappe.model.workflow.apply_workflow"):
			apply_workflow(doc, "Reject")

	as_user(APPROVER, reject_with_reason)
	rejected = fresh(draft)
	check(
		"a rejection with a reason goes back to Draft and says why",
		rejected.workflow_state == "Draft" and rejected.get(REJECTION_REASON_FIELD) == "Wrong cost centre",
	)

	as_user(PREPARER, lambda: apply_workflow(fresh(draft), "Submit for approval"))

	def approve():
		with method("frappe.model.workflow.apply_workflow"):
			apply_workflow(fresh(draft), "Approve")

	as_user(APPROVER, approve)
	posted = fresh(draft)
	check("approving posts the voucher", posted.docstatus == 1 and posted.workflow_state == "Approved")
	check(
		"and it is in the general ledger",
		frappe.db.count("GL Entry", {"voucher_no": posted.name, "is_cancelled": 0}) == 2,
	)
	check("the resubmission cleared last time's reason", not posted.get(REJECTION_REASON_FIELD))

	def preparer_cancels():
		with method("frappe.client.cancel"):
			fresh(draft).cancel()

	expect_throw("the preparer cannot reverse a posted voucher directly", lambda: as_user(PREPARER, preparer_cancels), frappe.PermissionError)

	def bulk_cancel():
		with method("frappe.desk.doctype.bulk_update.bulk_update.submit_cancel_or_update_docs"):
			submit_cancel_or_update_docs("Journal Entry", [draft.name], action="cancel")

	expect_throw("bulk cancel refuses an approval-governed voucher", lambda: as_user(APPROVER, bulk_cancel), frappe.PermissionError)
	check("it is still posted", frappe.db.get_value("Journal Entry", draft.name, "docstatus") == 1)

	print("\n--- an approver cannot approve their own voucher ---")
	own = as_user(APPROVER, make_voucher)
	as_user(APPROVER, lambda: apply_workflow(fresh(own), "Submit for approval"))
	expect_throw(
		"self-approval is refused",
		lambda: as_user(APPROVER, lambda: apply_workflow(fresh(own), "Approve")),
		frappe.ValidationError,
	)
	as_user(SECOND_APPROVER, lambda: apply_workflow(fresh(own), "Approve"))
	check("a second approver posts it", fresh(own).docstatus == 1)

	print("\n--- cancelling goes through the workflow too ---")
	as_user(APPROVER, lambda: apply_workflow(fresh(own), "Cancel"))
	check("Cancel reverses it", fresh(own).docstatus == 2 and fresh(own).workflow_state == "Cancelled")

	print("\n--- vouchers ERPNext generates inside another request still post ---")

	def generated():
		# The way HRMS posts a payroll accrual: saved with ignore_permissions, then submitted by
		# the same object, inside the request that posted the run.
		with method("folt_customizations.hr_portal.post_payroll"):
			doc = make_voucher(500)
			doc.flags.ignore_permissions = True
			doc.submit()
			return doc

	made = as_user(APPROVER, generated)
	check("a voucher submitted by server code in another request posts", made.docstatus == 1, made.name)

	def background():
		saved = getattr(frappe.local, "request", None)
		frappe.local.request = None
		try:
			doc = make_voucher(250)
			doc.flags.ignore_permissions = True  # how depreciation and the other scheduled vouchers save
			doc.submit()
			return doc
		finally:
			frappe.local.request = saved

	made = as_user(APPROVER, background)
	check("a voucher submitted outside any request (a job, the scheduler) posts", made.docstatus == 1, made.name)

	frappe.db.commit()
	teardown()
	print(f"\n  {len(PASS)} passed, {len(FAIL)} failed")
	if FAIL:
		print("  FAILED: " + "; ".join(FAIL))
	return {"passed": len(PASS), "failed": len(FAIL), "failures": FAIL}
