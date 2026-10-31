"""End-to-end check of finance_portal, the server half of the FoLT app's Finance module.

Four claims. Only the accounting roles get in, and the allow-lists hold: a salary slip is not a
Finance record, a payroll entry is not a link Finance may search. Every figure is ERPNext's own:
the reports are the native ones, run as the person; the dashboard's totals are the reports'
summaries. A voucher made from the app goes through the same approval as one made in the Desk.
And the auditor reads everything and changes nothing.

Idempotent: tears down its own documents first. Run with

    bench --site <site> execute folt_customizations.finance_portal_e2e.run
"""

import frappe
from frappe.utils import add_days, nowdate

from folt_customizations import finance_portal as fp

COMPANY = "FRIENDS of LAKE TURKANA"
MARK = "E2E finance portal"

PREPARER = "finofficer.test@folt.test"  # Finance Officer + Accounts User
APPROVER = "hof.test@folt.test"  # Head of Finance + Accounts Manager
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


def teardown():
	frappe.set_user("Administrator")
	for name in frappe.get_all("Journal Entry", filters={"user_remark": MARK}, pluck="name", order_by="creation desc"):
		doc = frappe.get_doc("Journal Entry", name)
		if doc.docstatus == 1:
			doc.flags.ignore_permissions = True
			doc.cancel()
		frappe.delete_doc("Journal Entry", name, force=True, ignore_permissions=True)
	frappe.db.commit()


def voucher(amount=2500):
	expense = frappe.db.get_value("Account", {"company": COMPANY, "account_name": "Travel Expenses", "is_group": 0})
	cash = frappe.db.get_value("Company", COMPANY, "default_cash_account")
	cost_center = frappe.db.get_value("Company", COMPANY, "cost_center")
	return {
		"company": COMPANY,
		"voucher_type": "Journal Entry",
		"posting_date": nowdate(),
		"user_remark": MARK,
		"accounts": [
			{"account": expense, "debit_in_account_currency": amount, "cost_center": cost_center},
			{"account": cash, "credit_in_account_currency": amount, "cost_center": cost_center},
		],
	}


def run():
	teardown()

	print("\n--- only the accounting roles get in ---")
	expect_throw("an employee without an accounting role is refused", lambda: as_user(OUTSIDER, fp.context), frappe.PermissionError)
	ctx = as_user(PREPARER, fp.context)
	check("the preparer gets the context", ctx["default_company"] == COMPANY, ctx["current_fiscal_year"])
	check("the preparer may create a journal entry", ctx["capabilities"]["Journal Entry"]["create"])
	check("the preparer is not an approver", not ctx["roles"]["approver"])
	check("the three approval workflows are named", {"Journal Entry", "Payment Entry", "Purchase Invoice"} <= set(ctx["workflows"]))
	check("the native reports are offered", {"General Ledger", "Trial Balance", "Balance Sheet", "Profit and Loss Statement", "Cash Flow"} <= set(ctx["reports"]), len(ctx["reports"]))
	audit_ctx = as_user(AUDITOR, fp.context)
	check("the auditor may read the ledger but not write a voucher", audit_ctx["capabilities"]["Journal Entry"]["read"] and not audit_ctx["capabilities"]["Journal Entry"]["create"])

	print("\n--- the allow-lists hold ---")
	expect_throw("a salary slip is not a Finance record", lambda: as_user(APPROVER, lambda: fp.records("Salary Slip")), frappe.PermissionError)
	expect_throw("a payroll entry is not a Finance link search", lambda: as_user(APPROVER, lambda: fp.link_search("Payroll Entry", "")), frappe.PermissionError)
	expect_throw("the salary register is not a Finance report", lambda: as_user(APPROVER, lambda: fp.run_report("Salary Register", {})), frappe.PermissionError)
	expect_throw("an arbitrary method is not a Finance lookup", lambda: as_user(APPROVER, lambda: fp.lookup("frappe.client.get_list", {"doctype": "Salary Slip"})), frappe.PermissionError)
	expect_throw("a submit is not a Finance operation", lambda: as_user(APPROVER, lambda: fp.operate("frappe.client.submit", {})), frappe.PermissionError)

	print("\n--- lists and records ---")
	page = as_user(PREPARER, lambda: fp.records("Account", q="Cash", filters=[["company", "=", COMPANY]], order_by="account_name asc", page_length=10))
	check("a list pages, searches and counts", page["total"] >= 1 and any("Cash" in r["name"] for r in page["rows"]), page["total"])
	expect_throw("an unknown filter field is refused", lambda: as_user(PREPARER, lambda: fp.records("Account", filters=[["password", "=", "x"]])))
	cash = frappe.db.get_value("Company", COMPANY, "default_cash_account")
	rec = as_user(PREPARER, lambda: fp.record("Account", cash))
	check("a record comes back with its permissions", rec["doc"]["name"] == cash and rec["permissions"]["read"])
	m = as_user(PREPARER, lambda: fp.meta("Journal Entry"))
	check("the doctype's fields and child tables come with it", "Journal Entry Account" in m["children"] and m["is_submittable"])
	settings = as_user(APPROVER, lambda: fp.record("Accounts Settings"))
	check("a single settings record reads by its doctype", settings["doc"]["doctype"] == "Accounts Settings")

	print("\n--- a voucher from the app: preview, save, approve ---")
	draft = voucher()
	draft["accounts"][1]["credit_in_account_currency"] = 2000
	preview = as_user(PREPARER, lambda: fp.preview("Journal Entry", draft))
	check("the preview is ERPNext's totals, unbalanced as typed", preview["total_debit"] == 2500 and preview["total_credit"] == 2000 and preview["difference"] == 500,
	      f"{preview['total_debit']} / {preview['total_credit']} / {preview['difference']}")
	check("the preview wrote nothing", not frappe.db.exists("Journal Entry", {"user_remark": MARK}))
	# ERPNext keeps an unbalanced draft (it refuses only at posting), so the app does too.
	unbalanced = as_user(PREPARER, lambda: fp.save("Journal Entry", draft))["name"]
	as_user(PREPARER, lambda: fp.act("Journal Entry", unbalanced, "transition", "Submit for approval"))
	expect_throw("an unbalanced voucher cannot be approved into the ledger", lambda: as_user(APPROVER, lambda: fp.act("Journal Entry", unbalanced, "transition", "Approve")))
	check("and stays unposted", frappe.db.get_value("Journal Entry", unbalanced, "docstatus") == 0)
	saved = as_user(PREPARER, lambda: fp.save("Journal Entry", voucher()))
	name = saved["name"]
	check("a balanced voucher saves as a draft", frappe.db.get_value("Journal Entry", name, "docstatus") == 0, name)
	expect_throw("the app cannot post it around the approval", lambda: as_user(PREPARER, lambda: fp.act("Journal Entry", name, "submit")), frappe.PermissionError)
	moves = as_user(PREPARER, lambda: fp.record("Journal Entry", name))["workflow"]["transitions"]
	check("the preparer is offered Submit for approval", [t["action"] for t in moves] == ["Submit for approval"], moves)
	as_user(PREPARER, lambda: fp.act("Journal Entry", name, "transition", "Submit for approval"))

	waiting = as_user(APPROVER, fp.approvals)["waiting"]
	check("it waits on the approver", any(w["name"] == name and "Approve" in w["moves"] for w in waiting), len(waiting))
	check("but not on the preparer", not any(w["name"] == name for w in as_user(PREPARER, fp.approvals)["waiting"]))
	check("the preparer sees it among their own", any(r["name"] == name for r in as_user(PREPARER, fp.approvals)["mine"]))

	turn_down = [t for t in as_user(APPROVER, lambda: fp.record("Journal Entry", name))["workflow"]["transitions"] if t["action"] == "Reject"]
	check("Reject is marked as needing a reason", turn_down and turn_down[0]["needs_reason"])
	expect_throw("a rejection without a reason is refused", lambda: as_user(APPROVER, lambda: fp.act("Journal Entry", name, "transition", "Reject")))
	as_user(APPROVER, lambda: fp.act("Journal Entry", name, "transition", "Reject", reason="Attach the receipt"))
	check("a rejection with a reason returns it", frappe.db.get_value("Journal Entry", name, "workflow_state") == "Draft")
	as_user(PREPARER, lambda: fp.act("Journal Entry", name, "transition", "Submit for approval"))
	as_user(APPROVER, lambda: fp.act("Journal Entry", name, "transition", "Approve"))
	posted = as_user(AUDITOR, lambda: fp.record("Journal Entry", name))
	check("approved, it is posted", posted["doc"]["docstatus"] == 1)
	check("and the record shows its ledger impact", len([g for g in posted["gl_entries"] if not g["is_cancelled"]]) == 2)
	check("and its history", any("Reject" in (c["content"] or "") or "Attach the receipt" in (c["content"] or "") for c in posted["timeline"]["comments"] + posted["timeline"]["workflow_logs"]))

	print("\n--- the auditor reads and changes nothing ---")
	expect_throw("the auditor cannot save a voucher", lambda: as_user(AUDITOR, lambda: fp.save("Journal Entry", voucher())), frappe.PermissionError)
	expect_throw("the auditor cannot cancel one", lambda: as_user(AUDITOR, lambda: fp.act("Journal Entry", name, "cancel")), frappe.PermissionError)
	trail = as_user(AUDITOR, lambda: fp.audit(doctype="Journal Entry"))
	check("the audit trail shows the voucher's workflow moves", any(e["name"] == name and e["kind"] == "workflow" for e in trail["events"]), len(trail["events"]))

	print("\n--- reports are ERPNext's own ---")
	from frappe.desk.query_report import run as native

	fy = ctx["current_fiscal_year"]
	start, end = frappe.db.get_value("Fiscal Year", fy, ["year_start_date", "year_end_date"])
	tb_filters = {"company": COMPANY, "fiscal_year": fy, "from_date": str(start), "to_date": str(end), "show_zero_values": 0}
	ours = as_user(AUDITOR, lambda: fp.run_report("Trial Balance", tb_filters))
	theirs = as_user(AUDITOR, lambda: native("Trial Balance", filters=tb_filters, ignore_prepared_report=True))
	check("the trial balance is the native one, row for row", ours["result"] == theirs["result"], len(ours["result"]))
	total = [r for r in ours["result"] if isinstance(r, dict) and r.get("account") == "'Total'"][0]
	check("and it balances", abs(total["closing_debit"] - total["closing_credit"]) < 0.005, f"{total['closing_debit']} = {total['closing_credit']}")
	gl = as_user(PREPARER, lambda: fp.run_report("General Ledger", {"company": COMPANY, "from_date": str(start), "to_date": str(end), "voucher_no": name, "group_by": "Group by Voucher (Consolidated)"}))
	check("the general ledger finds the voucher", any(isinstance(r, dict) and r.get("voucher_no") == name for r in gl["result"]))

	frappe.local.response = frappe._dict()
	as_user(AUDITOR, lambda: fp.export_report("Trial Balance", tb_filters, "csv"))
	check("the export is the report's rows as CSV", frappe.local.response.filename.endswith(".csv") and "Total" in frappe.local.response.filecontent)
	frappe.local.response = frappe._dict()
	as_user(AUDITOR, lambda: fp.export_report("Trial Balance", tb_filters, "xlsx"))
	check("and as a spreadsheet", frappe.local.response.filecontent[:2] == b"PK")
	frappe.local.response = frappe._dict()

	print("\n--- the dashboard is the reports' figures ---")
	dash = as_user(APPROVER, lambda: fp.dashboard(COMPANY, str(start), str(end)))
	pl = as_user(APPROVER, lambda: native("Profit and Loss Statement", filters={"company": COMPANY, "filter_based_on": "Date Range", "period_start_date": str(start),
	                                                                            "period_end_date": str(end), "periodicity": "Monthly"}, ignore_prepared_report=True))
	net = [r["value"] for r in pl["report_summary"] if r["label"] == "Net Profit"][0]
	check("net profit is the P&L report's own", dash["profit_and_loss"]["net_profit"] == net, net)
	check("the balance sheet totals are there", dash["balance_sheet"]["assets"] is not None)
	check("cash and bank balances are listed", any(a["name"] == cash for a in dash["cash_and_bank"]))
	check("no section failed", not dash["unavailable"], dash["unavailable"])
	tree = as_user(PREPARER, lambda: fp.account_tree(COMPANY))
	check("the chart of accounts has balances", any(a["name"] == cash and a["balance"] for a in tree["accounts"]))
	review = as_user(APPROVER, lambda: fp.closing_review(COMPANY, fy))
	check("the closing review counts and checks the trial balance", review["trial_balance"]["balanced"] and len(review["checks"]) >= 6)

	print("\n--- lookups are ERPNext's own helpers ---")
	bal = as_user(PREPARER, lambda: fp.lookup("erpnext.accounts.utils.get_balance_on", {"account": cash, "date": add_days(nowdate(), 1)}))
	check("a balance lookup answers", isinstance(bal, (int, float)), bal)

	teardown()
	print(f"\n  {len(PASS)} passed, {len(FAIL)} failed")
	if FAIL:
		print("  FAILED: " + "; ".join(FAIL))
	return {"passed": len(PASS), "failed": len(FAIL), "failures": FAIL}
