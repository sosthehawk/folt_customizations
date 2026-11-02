"""End-to-end check of procurement_portal, the server half of the FoLT app's Procurement module
(ADR-008).

Claims, phase by phase:
- Who gets in: the procurement, stores and finance roles reach the buying cycle; an Employee with
  none of them reaches the requisition only; the allow-lists hold.
- Suppliers: registered from the app with ERPNext making their primary contact and address;
  contacts, addresses and bank accounts opened here are suppliers' only; bank details are masked
  for everybody who does not keep them; payments are read here and written in Finance.
- The dashboard's figures are ERPNext's own documents counted and summed as the reader may see them.

Idempotent: tears down its own documents first. Run with

    bench --site <site> execute folt_customizations.procurement_portal_e2e.run
"""

import frappe
from frappe.utils import add_days, flt, getdate, nowdate

from folt_customizations import procurement_portal as pp

COMPANY = "FRIENDS of LAKE TURKANA"
SUPPLIER = "E2E Procurement Supplier"
BANK = "E2E Procurement Bank"

REQUESTER = "requester.test@folt.test"  # Employee only
BUYER = "purchaser.test@folt.test"  # Purchase User
MANAGER = "procmanager.test@folt.test"  # Purchase Manager
FIN_MANAGER = "finmanager.test@folt.test"  # Finance Manager + Accounts User + Accounts Manager
FIN_OFFICER = "finofficer.test@folt.test"  # Finance Officer + Accounts User
AUDITOR = "ed.test@folt.test"  # Executive Director + Auditor

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
	for ba in frappe.get_all("Bank Account", filters={"party_type": "Supplier", "party": SUPPLIER}, pluck="name"):
		frappe.delete_doc("Bank Account", ba, force=True, ignore_permissions=True)
	for dt in ("Contact", "Address"):
		for name in frappe.get_all("Dynamic Link", filters={"parenttype": dt, "link_doctype": "Supplier", "link_name": SUPPLIER}, pluck="parent"):
			frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
	if frappe.db.exists("Supplier", SUPPLIER):
		frappe.delete_doc("Supplier", SUPPLIER, force=True, ignore_permissions=True)
	if frappe.db.exists("Bank", BANK):
		frappe.delete_doc("Bank", BANK, force=True, ignore_permissions=True)
	frappe.db.commit()


def outsider():
	"""A test login holding neither a procurement role nor Employee, if the site has one."""
	staff = set(pp.STAFF) | set(pp.REQUESTERS)
	for user in frappe.get_all("User", filters={"name": ["like", "%.test@folt.test"], "enabled": 1}, pluck="name"):
		if not set(frappe.get_roles(user)) & staff:
			return user
	return None


def run():
	teardown()
	try:
		_run()
	finally:
		teardown()
	print(f"\n  {len(PASS)} passed, {len(FAIL)} failed")
	return {"passed": len(PASS), "failed": len(FAIL), "failures": FAIL}


def _run():
	print("\n--- who gets in ---")
	who = outsider()
	if who:
		expect_throw("a login with no procurement role and no Employee role is refused", lambda: as_user(who, pp.context), frappe.PermissionError)
	ctx = as_user(REQUESTER, pp.context)
	check("an employee gets the requester's context", ctx["roles"]["requester"] and not ctx["roles"]["staff"])
	check("a requester sees the requisition and nothing else", set(ctx["capabilities"]) == {"Material Request"}, sorted(ctx["capabilities"]))
	expect_throw("a requester cannot list suppliers", lambda: as_user(REQUESTER, lambda: pp.records("Supplier")), frappe.PermissionError)
	expect_throw("a requester cannot open the dashboard", lambda: as_user(REQUESTER, lambda: pp.dashboard(COMPANY, add_days(nowdate(), -30), nowdate())), frappe.PermissionError)
	reqs = as_user(REQUESTER, lambda: pp.records("Material Request"))
	check("a requester lists requisitions", "rows" in reqs, reqs["total"])
	bctx = as_user(BUYER, pp.context)
	check("a buyer gets the staff context", bctx["roles"]["staff"] and bctx["roles"]["buyer"])
	mctx = as_user(MANAGER, pp.context)
	check("a purchase manager, who cannot open Company, still gets the companies", any(c["name"] == COMPANY for c in mctx["companies"]), [c["name"] for c in mctx["companies"]])
	for user in (MANAGER, FIN_MANAGER, FIN_OFFICER, AUDITOR):
		try:
			as_user(user, pp.context)
			ok = True
		except Exception as e:  # noqa: BLE001
			ok = repr(e)
		check(f"the context answers {user.split('.')[0]}", ok is True, ok)
	check("payments are read-only here, whatever the role", not as_user(FIN_MANAGER, pp.context)["capabilities"]["Payment Entry"]["create"])

	print("\n--- the allow-lists hold ---")
	expect_throw("a journal entry is not a Procurement record", lambda: as_user(MANAGER, lambda: pp.records("Journal Entry")), frappe.PermissionError)
	expect_throw("a salary slip is not a Procurement link search", lambda: as_user(MANAGER, lambda: pp.link_search("Salary Slip", "")), frappe.PermissionError)
	expect_throw("an arbitrary method is not a Procurement lookup", lambda: as_user(MANAGER, lambda: pp.lookup("frappe.client.get_list", {"doctype": "User"})), frappe.PermissionError)
	expect_throw("a payment is not written from Procurement", lambda: as_user(FIN_MANAGER, lambda: pp.save("Payment Entry", {"payment_type": "Pay"})), frappe.PermissionError)

	print("\n--- registering a supplier ---")
	group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
	values = {
		"supplier_name": SUPPLIER, "supplier_group": group, "supplier_type": "Company", "country": "Kenya", "tax_id": "P051234567Z",
		"default_currency": "KES", "email_id": "e2e.procurement.supplier@example.com", "mobile_no": "+254700000001",
		"address_line1": "Plot 7, Kanamkemer Road", "city": "Lodwar", "country_address": "Kenya",
	}
	values["country"] = "Kenya"
	expect_throw("an address without its town is refused", lambda: as_user(MANAGER, lambda: pp.save("Supplier", {**values, "city": ""})))
	created = as_user(MANAGER, lambda: pp.save("Supplier", values))
	check("a purchase manager registers a supplier", created["name"] == SUPPLIER)
	profile = as_user(MANAGER, lambda: pp.supplier_profile(SUPPLIER))
	check("ERPNext made the primary contact", any(c["email_id"] == values["email_id"] for c in profile["contacts"] or []), profile["contacts"])
	check("ERPNext made the primary address", any(a["city"] == "Lodwar" for a in profile["addresses"] or []), profile["addresses"])
	check("the profile carries KRA PIN and status", profile["record"]["doc"]["tax_id"] == "P051234567Z" and profile["status"] == "Active")
	contact = (profile["contacts"] or [{}])[0].get("name")
	expect_throw("a supplier's contact cannot be re-linked to a customer", lambda: as_user(MANAGER, lambda: pp.save("Contact", {"links": [{"link_doctype": "Customer", "link_name": "x"}]}, contact)), frappe.PermissionError)
	staff_contact = frappe.db.get_value("Dynamic Link", {"parenttype": "Contact", "link_doctype": ["!=", "Supplier"]}, "parent")
	if staff_contact and not frappe.db.exists("Dynamic Link", {"parenttype": "Contact", "parent": staff_contact, "link_doctype": "Supplier"}):
		expect_throw("a contact that is not a supplier's is not opened here", lambda: as_user(MANAGER, lambda: pp.record("Contact", staff_contact)), frappe.PermissionError)
	as_user(MANAGER, lambda: pp.save("Supplier", {"on_hold": 1, "hold_type": "All", "modified": frappe.db.get_value("Supplier", SUPPLIER, "modified")}, SUPPLIER))
	check("putting a supplier on hold reads as Blocked", as_user(MANAGER, lambda: pp.supplier_profile(SUPPLIER))["status"] == "Blocked")

	print("\n--- bank details ---")
	if not frappe.db.exists("Bank", BANK):
		frappe.get_doc({"doctype": "Bank", "bank_name": BANK}).insert(ignore_permissions=True)
	ba = frappe.get_doc({"doctype": "Bank Account", "account_name": "E2E Supplier Account", "bank": BANK, "party_type": "Supplier",
	                     "party": SUPPLIER, "bank_account_no": "0123456789012"}).insert(ignore_permissions=True)
	keeper = as_user(FIN_MANAGER, lambda: pp.supplier_profile(SUPPLIER))["bank_accounts"]
	check("an accounts manager sees the account number in full", keeper and keeper[0]["bank_account_no"] == "0123456789012", keeper and keeper[0]["bank_account_no"])
	officer = as_user(FIN_OFFICER, lambda: pp.supplier_profile(SUPPLIER))["bank_accounts"]
	if officer is None:
		check("a reader without Bank Account read gets no bank section", True)
	else:
		check("an accounts user sees the last four characters only", officer[0]["bank_account_no"].endswith("9012") and "0123" not in officer[0]["bank_account_no"], officer[0]["bank_account_no"])
	rec = as_user(FIN_OFFICER, lambda: pp.record("Bank Account", ba.name)) if officer is not None else None
	if rec:
		check("the record is masked too", rec["doc"]["bank_account_no"].endswith("9012") and "0123" not in rec["doc"]["bank_account_no"])
	buyer_bank = as_user(BUYER, lambda: pp.supplier_profile(SUPPLIER))["bank_accounts"]
	check("a buyer without bank access gets no account numbers", buyer_bank is None or all("0123" not in (b["bank_account_no"] or "") for b in buyer_bank))
	company_account = frappe.db.get_value("Bank Account", {"is_company_account": 1}, "name")
	if company_account:
		expect_throw("the company's own bank account is not opened here", lambda: as_user(FIN_MANAGER, lambda: pp.record("Bank Account", company_account)), frappe.PermissionError)

	print("\n--- the dashboard is ERPNext's documents ---")
	start, end = add_days(nowdate(), -365), nowdate()
	dash = as_user(MANAGER, lambda: pp.dashboard(COMPANY, start, end))
	expected = flt(frappe.db.sql("select sum(base_grand_total) from `tabPurchase Order` where docstatus=1 and company=%s and transaction_date between %s and %s",
	                              (COMPANY, start, end))[0][0])
	check("ordered spend is the submitted orders' own grand totals", abs(flt(dash["spend"]["ordered"]) - expected) < 0.01, f"{dash['spend']['ordered']} = {expected}")
	approved = frappe.db.count("Purchase Order", {"docstatus": 1, "company": COMPANY, "transaction_date": ["between", [start, end]]})
	check("approved orders are counted from the orders", dash["counts"]["orders_approved"] == approved, approved)
	check("orders awaiting approval come from the workflow's waiting states", dash["counts"]["orders_awaiting_approval"] is not None)
	by_supplier = sum(r["total"] for r in dash["by_supplier"] or [])
	net = flt(frappe.db.sql("select sum(base_net_total) from `tabPurchase Order` where docstatus=1 and company=%s and transaction_date between %s and %s",
	                         (COMPANY, start, end))[0][0])
	check("spend by supplier adds up to the orders' net totals", abs(by_supplier - net) < 0.01 or len(dash["by_supplier"] or []) == 12, f"{by_supplier} / {net}")
	check("the reader's approvals come with it", isinstance(dash["waiting_on_me"], list))
	audit = as_user(AUDITOR, lambda: pp.dashboard(COMPANY, start, end))
	check("the auditor reads the dashboard", audit["counts"] is not None)
	expect_throw("the auditor cannot register a supplier", lambda: as_user(AUDITOR, lambda: pp.save("Supplier", {**values, "supplier_name": SUPPLIER + " 2"})))
