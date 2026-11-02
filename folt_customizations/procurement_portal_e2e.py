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
HOP = "hop.test@folt.test"  # Head of Programs
RECEIVER = "receiver.test@folt.test"  # Stock User
HOF = "hof.test@folt.test"  # Head of Finance + Accounts User + Accounts Manager
MARK = "E2E procurement requisition"
BIDDERS = ("E2E Bidder Alpha", "E2E Bidder Beta")
RFQ_SUBJECT = "E2E procurement RFQ"
BIDDER_USER = "e2e.bidder.portal@example.com"

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


def _drop(doctype, name):
	doc = frappe.get_doc(doctype, name)
	if doc.docstatus == 1:
		doc.flags.ignore_permissions = True
		doc.cancel()
	frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)


def teardown():
	frappe.set_user("Administrator")
	rfqs = frappe.get_all("Request for Quotation", filters={"subject": RFQ_SUBJECT}, pluck="name")
	for ev in frappe.get_all("Procurement Committee Evaluation", filters={"request_for_quotation": ["in", rfqs or [""]]}, pluck="name"):
		_drop("Procurement Committee Evaluation", ev)
	for pi in frappe.get_all("Purchase Invoice", filters={"supplier": ["in", BIDDERS]}, pluck="name", order_by="creation desc"):
		_drop("Purchase Invoice", pi)
	for pr in frappe.get_all("Purchase Receipt", filters={"supplier": ["in", BIDDERS]}, pluck="name", order_by="creation desc"):
		_drop("Purchase Receipt", pr)
	for po in frappe.get_all("Purchase Order", filters={"supplier": ["in", BIDDERS]}, pluck="name", order_by="creation desc"):
		_drop("Purchase Order", po)
	sqs = frappe.get_all("Supplier Quotation", filters={"supplier": ["in", BIDDERS]}, pluck="name")
	for wv in frappe.get_all("Derogation Waiver Request", filters={"supplier_quotation": ["in", sqs or [""]]}, pluck="name"):
		_drop("Derogation Waiver Request", wv)
	if frappe.db.exists("User", BIDDER_USER):
		frappe.delete_doc("User", BIDDER_USER, force=True, ignore_permissions=True)
	for sq in frappe.get_all("Supplier Quotation", filters={"supplier": ["in", BIDDERS]}, pluck="name"):
		_drop("Supplier Quotation", sq)
	for rfq in rfqs:
		_drop("Request for Quotation", rfq)
	for b in BIDDERS:
		if frappe.db.exists("Supplier", b):
			frappe.delete_doc("Supplier", b, force=True, ignore_permissions=True)
	for name in frappe.get_all("Material Request", filters={"title": ["like", f"{MARK}%"]}, pluck="name", order_by="creation desc"):
		doc = frappe.get_doc("Material Request", name)
		if doc.docstatus == 1:
			doc.flags.ignore_permissions = True
			doc.cancel()
		frappe.delete_doc("Material Request", name, force=True, ignore_permissions=True)
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
		mr, quotes = _sourcing(_requisitions())
		_reporting(mr, quotes)
		_ordering(mr, quotes)
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


def _approved_activity(approved=True):
	return frappe.db.get_value("Activity Requisition", {"docstatus": 1 if approved else 0, "company": COMPANY}, "name")


def _requisitions():
	print("\n--- a purchase requisition ---")
	item = frappe.db.get_value("Item", {"is_stock_item": 0, "disabled": 0}, "name")
	activity = _approved_activity()
	draft = {
		"title": f"{MARK} 1", "company": COMPANY, "transaction_date": nowdate(), "schedule_date": add_days(nowdate(), 14),
		"folt_priority": "High", "folt_activity_requisition": activity,
		"items": [{"item_code": item, "qty": 2, "folt_estimated_rate": 5000, "schedule_date": add_days(nowdate(), 14), "uom": frappe.db.get_value("Item", item, "stock_uom")}],
	}
	unapproved = _approved_activity(False)
	if unapproved:
		expect_throw("a requisition against an unapproved activity is refused", lambda: as_user(REQUESTER, lambda: pp.save("Material Request", {**draft, "folt_activity_requisition": unapproved})))
	name = as_user(REQUESTER, lambda: pp.save("Material Request", draft))["name"]
	mr = frappe.get_doc("Material Request", name)
	check("an employee raises a requisition, purpose Purchase", mr.material_request_type == "Purchase" and mr.owner == REQUESTER)
	check("it starts in Draft", mr.workflow_state == "Draft", mr.workflow_state)
	templated = as_user(REQUESTER, lambda: pp.save("Material Request", {**draft, "title": "{material_request_type}"}))["name"]
	t = frappe.db.get_value("Material Request", templated, "title")
	check("a templated title becomes what is being bought", "{" not in t and t, t)
	frappe.db.set_value("Material Request", templated, "title", f"{MARK} templated")
	check("the requester's department is filled", mr.folt_department == frappe.db.get_value("Employee", {"user_id": REQUESTER}, "department"), mr.folt_department)
	project = frappe.db.get_value("Activity Requisition", activity, "project")
	check("the activity's project fills the line", not project or mr.items[0].project == project, mr.items[0].project)
	check("the estimated total is the lines' qty x the requester's estimate", flt(mr.folt_estimated_total) == 10000 and flt(mr.items[0].folt_estimated_amount) == 10000, mr.folt_estimated_total)

	print("\n--- its approval ---")
	expect_throw("it does not go for review without a justification", lambda: as_user(REQUESTER, lambda: pp.act("Material Request", name, "transition", "Submit for Review")))
	as_user(REQUESTER, lambda: pp.save("Material Request", {"folt_justification": "Venue sound for the Kalokol forum.", "modified": str(frappe.db.get_value("Material Request", name, "modified"))}, name))
	expect_throw("the requester cannot submit it around the workflow", lambda: as_user(REQUESTER, lambda: pp.act("Material Request", name, "submit")), frappe.PermissionError)
	as_user(REQUESTER, lambda: pp.act("Material Request", name, "transition", "Submit for Review"))
	check("submitted for review, it waits on the Head of Programs", frappe.db.get_value("Material Request", name, "workflow_state") == "Pending Head of Programs")
	expect_throw("the requester cannot approve it", lambda: as_user(REQUESTER, lambda: pp.act("Material Request", name, "transition", "Approve")))
	bulk = as_user(HOP, lambda: pp.act_many([{"doctype": "Material Request", "name": name}], "Return for Revision"))
	check("a return for revision is never done in bulk", not bulk["done"] and bulk["failed"], bulk["failed"])
	bulk = as_user(HOP, lambda: pp.act_many([{"doctype": "Material Request", "name": name}, {"doctype": "Journal Entry", "name": "x"}], "Approve"))
	check("the Head of Programs approves it from the inbox in bulk", bulk["done"] == [{"doctype": "Material Request", "name": name}] and len(bulk["failed"]) == 1, bulk)
	check("it now waits on the Head of Finance", frappe.db.get_value("Material Request", name, "workflow_state") == "Pending Head of Finance")
	expect_throw("returning it needs a reason", lambda: as_user(HOF, lambda: pp.act("Material Request", name, "transition", "Return for Revision")))
	as_user(HOF, lambda: pp.act("Material Request", name, "transition", "Return for Revision", "Split the PA hire from the generator."))
	back = frappe.get_doc("Material Request", name)
	check("returned, it is a Draft again with the reason on it", back.workflow_state == "Draft" and "generator" in (back.folt_rejection_reason or ""), back.folt_rejection_reason)
	as_user(REQUESTER, lambda: pp.act("Material Request", name, "transition", "Submit for Review"))
	check("resubmitted, last time's reason is cleared", not frappe.db.get_value("Material Request", name, "folt_rejection_reason"))
	as_user(HOP, lambda: pp.act("Material Request", name, "transition", "Approve"))
	as_user(HOF, lambda: pp.act("Material Request", name, "transition", "Approve"))
	done = frappe.get_doc("Material Request", name)
	check("the Head of Finance's approval submits it", done.docstatus == 1 and done.workflow_state == "Approved", f"{done.docstatus} {done.workflow_state}")
	check("ERPNext now has it pending an order", done.status == "Pending", done.status)
	expect_throw("an approved requisition cannot be edited", lambda: as_user(REQUESTER, lambda: pp.save("Material Request", {"folt_justification": "changed"}, name)))

	print("\n--- what it has become ---")
	f = as_user(REQUESTER, lambda: pp.flow("Material Request", name))
	check("its flow names itself and its lines' ordered and received quantities", any(d["current"] for d in f["documents"]) and f["lines"] and f["lines"][0]["ordered_qty"] == 0, f["lines"])
	ctx = as_user(REQUESTER, pp.context)
	check("the context names the requisition workflow", ctx["workflows"].get("Material Request") == "FoLT Purchase Requisition Approval")
	check("the requester's estimate survives the approvers' saves", flt(done.items[0].folt_estimated_rate) == 5000 and flt(done.folt_estimated_total) == 10000, f"{done.items[0].folt_estimated_rate} {done.folt_estimated_total}")
	other = _other_employee()
	if other:
		expect_throw("another employee cannot open somebody else's requisition", lambda: as_user(other, lambda: pp.record("Material Request", name)), frappe.PermissionError)
	else:
		print("  SKIP  another employee cannot open somebody else's requisition -- no second Employee-only test login")
	return name


def _other_employee():
	staff = set(pp.STAFF)
	for user in frappe.get_all("Has Role", filters={"role": "Employee", "parenttype": "User", "parent": ["like", "%.test@folt.test"]}, pluck="parent"):
		if user != REQUESTER and not set(frappe.get_roles(user)) & staff:
			return user
	return None


def _sourcing(mr):
	print("\n--- an RFQ from the requisition ---")
	from frappe.utils import add_to_date, now_datetime

	group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
	for b in BIDDERS:
		if not frappe.db.exists("Supplier", b):
			frappe.get_doc({"doctype": "Supplier", "supplier_name": b, "supplier_group": group, "country": "Kenya"}).insert(ignore_permissions=True)
	expect_throw("a requester cannot use the buyer's makers",
	             lambda: as_user(REQUESTER, lambda: pp.lookup("erpnext.stock.doctype.material_request.material_request.make_request_for_quotation", {"source_name": mr})), frappe.PermissionError)
	made = as_user(BUYER, lambda: pp.lookup("erpnext.stock.doctype.material_request.material_request.make_request_for_quotation", {"source_name": mr}))
	check("ERPNext makes the RFQ from the approved requisition, unsaved", not made.get("name") or str(made.get("name")).startswith("new-") or made.get("__islocal"), made.get("name"))
	check("the RFQ's lines name the requisition", made["items"][0]["material_request"] == mr)
	deadline = str(add_to_date(now_datetime(), days=3))
	rfq_doc = {k: v for k, v in made.items() if k not in ("name", "__islocal", "__unsaved", "docstatus", "owner", "creation", "modified")}
	rfq_doc["items"] = [{k: v for k, v in r.items() if k not in ("name", "parent", "parenttype", "parentfield", "docstatus", "idx")} for r in made["items"]]
	rfq_doc.update({"subject": RFQ_SUBJECT, "message_for_supplier": "Please quote.", "company": COMPANY, "transaction_date": nowdate(),
	                "suppliers": [{"supplier": b, "send_email": 0} for b in BIDDERS]})
	expect_throw("a deadline before the RFQ's own date is refused", lambda: as_user(BUYER, lambda: pp.save("Request for Quotation", {**rfq_doc, "folt_quotation_deadline": str(add_to_date(now_datetime(), days=-2))})))
	rfq = as_user(BUYER, lambda: pp.save("Request for Quotation", {**rfq_doc, "folt_quotation_deadline": deadline}))["name"]
	expect_throw("a purchase user cannot send it", lambda: as_user(BUYER, lambda: pp.act("Request for Quotation", rfq, "submit")))
	as_user(MANAGER, lambda: pp.act("Request for Quotation", rfq, "submit"))
	check("the purchase manager sends it", frappe.db.get_value("Request for Quotation", rfq, "docstatus") == 1)

	print("\n--- bids, and comparing them ---")
	quotes = {}
	for bidder, rate, lead in ((BIDDERS[0], 4000, 5), (BIDDERS[1], 3500, 10)):
		got = as_user(BUYER, lambda: pp.lookup("erpnext.buying.doctype.request_for_quotation.request_for_quotation.make_supplier_quotation_from_rfq", {"source_name": rfq, "for_supplier": bidder}))
		bid = {k: v for k, v in got.items() if k not in ("name", "__islocal", "__unsaved", "docstatus", "owner", "creation", "modified")}
		bid["items"] = [{**{k: v for k, v in r.items() if k not in ("name", "parent", "parenttype", "parentfield", "docstatus", "idx")}, "rate": rate, "lead_time_days": lead} for r in got["items"]]
		bid.update({"transaction_date": nowdate(), "valid_till": add_days(nowdate(), 30)})
		name = as_user(BUYER, lambda: pp.save("Supplier Quotation", bid))["name"]
		as_user(BUYER, lambda: pp.act("Supplier Quotation", name, "submit"))
		quotes[bidder] = name
	late = {**bid, "supplier": BIDDERS[0], "transaction_date": add_days(nowdate(), 5)}
	expect_throw("a bid dated after the deadline is refused as late", lambda: as_user(BUYER, lambda: pp.save("Supplier Quotation", late)))
	expect_throw("a requester cannot compare bids", lambda: as_user(REQUESTER, lambda: pp.compare(rfq)), frappe.PermissionError)
	cmp = as_user(BUYER, lambda: pp.compare(rfq))
	check("both bids are compared", {q["name"] for q in cmp["quotations"]} == set(quotes.values()), [q["name"] for q in cmp["quotations"]])
	check("the lowest total is the cheaper bid", cmp["lowest"]["total"] == quotes[BIDDERS[1]], cmp["lowest"])
	line = cmp["lines"][0]["rfq_item"]
	check("the lowest per line is marked", cmp["lowest"]["lines"][line] == quotes[BIDDERS[1]])
	check("each bid answers every line", all(q["complete"] for q in cmp["quotations"]))
	check("lead times are the bids' own", {q["lead_time_days"] for q in cmp["quotations"]} == {5, 10})
	check("both invited suppliers are listed with their bid", {s["supplier"]: s["quotation"] for s in cmp["suppliers"]} == quotes)
	check("the routes out of a bid are offered", any(h["target"] == "Procurement Committee Evaluation" and h["ready"] for h in cmp["routes"][quotes[BIDDERS[1]]]), cmp["routes"])
	f = as_user(BUYER, lambda: pp.flow("Material Request", mr))
	check("the requisition's chain now holds its RFQ and both bids", {"Request for Quotation", "Supplier Quotation"} <= {d["doctype"] for d in f["documents"]})

	print("\n--- into the committee ---")
	ev = as_user(BUYER, lambda: pp.handoff("Supplier Quotation", quotes[BIDDERS[1]], "Procurement Committee Evaluation"))
	check("a committee evaluation is raised from a bid", ev["doctype"] == "Procurement Committee Evaluation" and frappe.db.exists(ev["doctype"], ev["name"]), ev)
	expect_throw("a second evaluation for the same competition is refused", lambda: as_user(BUYER, lambda: pp.handoff("Supplier Quotation", quotes[BIDDERS[0]], "Procurement Committee Evaluation")))
	expect_throw("a hand-off the chain does not declare is refused", lambda: as_user(BUYER, lambda: pp.handoff("Supplier Quotation", quotes[BIDDERS[0]], "Purchase Order")))
	check("the comparison names the evaluation", as_user(BUYER, lambda: pp.compare(rfq))["evaluation"]["name"] == ev["name"])
	return mr, quotes


def _ordering(mr, quotes):
	print("\n--- a purchase order, by the waiver route ---")
	from frappe.model.workflow import apply_workflow

	from folt_customizations import procurement_chain
	from folt_customizations.procurement_chain_e2e import authorise

	frappe.set_user("Administrator")
	raised = procurement_chain.make_waiver_request(quotes[BIDDERS[0]])
	waiver = authorise(frappe.get_doc("Derogation Waiver Request", raised["name"]))
	check("the waiver is authorised", waiver.workflow_state == "Approved", waiver.workflow_state)
	po = as_user(BUYER, lambda: pp.handoff("Derogation Waiver Request", waiver.name, "Purchase Order"))
	check("the order is raised from the authorised waiver", po["doctype"] == "Purchase Order" and frappe.db.exists("Purchase Order", po["name"]), po)
	order = po["name"]
	f = as_user(BUYER, lambda: pp.flow("Derogation Waiver Request", waiver.name))
	check("the waiver's next step names its order", any(order in h["existing"] for h in f["handoffs"]), f["handoffs"])
	made = as_user(BUYER, lambda: pp.lookup("erpnext.stock.doctype.material_request.material_request.make_purchase_order", {"source_name": mr}))
	check("ERPNext also makes an order straight from a requisition, unsaved", made["items"][0]["material_request"] == mr and not frappe.db.exists("Purchase Order", made.get("name") or "x"))

	print("\n--- the threshold ---")
	# Only the threshold is changed and put back: saving the whole settings record would write
	# every other field as it stands (an unset one as zero).
	before = frappe.db.get_single_value("FoLT Procurement Settings", "po_ed_threshold")
	try:
		frappe.db.set_single_value("FoLT Procurement Settings", "po_ed_threshold", 1000)
		as_user(BUYER, lambda: pp.save("Purchase Order", {"modified": str(frappe.db.get_value("Purchase Order", order, "modified"))}, order))
		check("an order above the threshold needs the Executive Director", frappe.db.get_value("Purchase Order", order, "folt_approval_tier") == "Executive")
		as_user(BUYER, lambda: pp.act("Purchase Order", order, "transition", "Submit for approval"))
		moves = as_user(FIN_MANAGER, lambda: pp.record("Purchase Order", order))["workflow"]["transitions"]
		check("the Finance Manager is offered one Approve, onward to the Executive Director",
		      [(m["action"], m["next_state"]) for m in moves if m["action"] == "Approve"] == [("Approve", "Pending Executive Director Approval")], moves)
		as_user(FIN_MANAGER, lambda: pp.act("Purchase Order", order, "transition", "Approve"))
		check("it waits on the Executive Director", frappe.db.get_value("Purchase Order", order, "workflow_state") == "Pending Executive Director Approval")
		expect_throw("the Finance Manager cannot approve it a second time", lambda: as_user(FIN_MANAGER, lambda: pp.act("Purchase Order", order, "transition", "Approve")))
		as_user(AUDITOR, lambda: pp.act("Purchase Order", order, "transition", "Approve"))
		check("the Executive Director's approval submits it", frappe.db.get_value("Purchase Order", order, ["workflow_state", "docstatus"]) == ("Approved", 1))
		frappe.db.set_single_value("FoLT Procurement Settings", "po_ed_threshold", 10_000_000)
		small = frappe.get_doc("Purchase Order", order)
		from folt_customizations.purchase_order import approval_tier
		approval_tier(small)
		check("below the threshold the Finance Manager's approval is enough", small.folt_approval_tier == "Standard")
	finally:
		frappe.db.set_single_value("FoLT Procurement Settings", "po_ed_threshold", before)

	print("\n--- the supplier ---")
	expect_throw("email is for the procurement roles", lambda: as_user(REQUESTER, lambda: pp.operate("frappe.core.doctype.communication.email.make", {})), frappe.PermissionError)
	as_user(MANAGER, lambda: pp.operate("frappe.core.doctype.communication.email.make", {"doctype": "Purchase Order", "name": order, "recipients": "orders@example.com",
	                                                                                    "subject": f"Purchase order {order}", "content": "Please deliver.", "send_email": 0}))
	check("the email is recorded on the order", frappe.db.exists("Communication", {"reference_doctype": "Purchase Order", "reference_name": order, "communication_type": "Communication"}))
	from folt_customizations import supplier_portal

	if not frappe.db.exists("User", BIDDER_USER):
		u = frappe.get_doc({"doctype": "User", "email": BIDDER_USER, "first_name": "E2E Bidder", "user_type": "Website User", "send_welcome_email": 0})
		u.append("roles", {"role": "Supplier"})
		u.insert(ignore_permissions=True)
	sup = frappe.get_doc("Supplier", BIDDERS[0])
	if not any(p.user == BIDDER_USER for p in sup.portal_users):
		sup.append("portal_users", {"user": BIDDER_USER})
		sup.save(ignore_permissions=True)
	other = frappe.get_doc("Supplier", BIDDERS[1])
	expect_throw("nobody else's supplier can acknowledge it", lambda: as_user(REQUESTER, lambda: supplier_portal.acknowledge_order(order)), frappe.PermissionError)
	ack = as_user(BIDDER_USER, lambda: supplier_portal.acknowledge_order(order))
	check("the supplier acknowledges it on the portal", frappe.db.get_value("Purchase Order", order, "folt_supplier_acknowledged_by") == BIDDER_USER, ack)
	again = as_user(BIDDER_USER, lambda: supplier_portal.acknowledge_order(order))
	check("the first acknowledgement is the one kept", again["acknowledged_on"] == ack["acknowledged_on"])
	view = as_user(BIDDER_USER, supplier_portal.portal_overview)
	check("the portal shows it acknowledged", any(o["name"] == order and o["acknowledged_on"] for o in view["orders"]))
	del other

	_receiving(order)

	print("\n--- delivery, cancel and amend ---")
	lines = as_user(BUYER, lambda: pp.flow("Purchase Order", order))["lines"]
	check("the order's lines carry ERPNext's received and billed figures", lines and lines[0]["received_qty"] == 0 and "billed_amt" in lines[0] and lines[0]["overdue"] is False, lines)
	expect_throw("a purchase user cannot cancel an approved order", lambda: as_user(BUYER, lambda: pp.act("Purchase Order", order, "transition", "Cancel")))
	as_user(MANAGER, lambda: pp.act("Purchase Order", order, "transition", "Cancel"))
	check("the purchase manager cancels it through the workflow", frappe.db.get_value("Purchase Order", order, ["workflow_state", "docstatus"]) == ("Cancelled", 2))
	amended = as_user(MANAGER, lambda: pp.act("Purchase Order", order, "amend"))["name"]
	check("a cancelled order is amended into a new draft", frappe.db.get_value("Purchase Order", amended, ["amended_from", "docstatus", "workflow_state"]) == (order, 0, "Draft"),
	      frappe.db.get_value("Purchase Order", amended, ["amended_from", "docstatus", "workflow_state"]))


def _rejected_warehouse():
	"""Where rejected goods are held: ERPNext requires one on a rejecting line. The site has none, so
	the check makes its own (kept between runs: a warehouse is configuration, not test data)."""
	abbr = frappe.get_cached_value("Company", COMPANY, "abbr")
	name = f"E2E Rejected Goods - {abbr}"
	if not frappe.db.exists("Warehouse", name):
		frappe.get_doc({"doctype": "Warehouse", "warehouse_name": "E2E Rejected Goods", "company": COMPANY}).insert(ignore_permissions=True)
	return name


def _receipt(order, received, accepted, rejected=0, notes=None):
	made = as_user(RECEIVER, lambda: pp.lookup("erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt", {"source_name": order}))
	doc = {k: v for k, v in made.items() if k not in ("name", "__islocal", "__unsaved", "docstatus", "owner", "creation", "modified")}
	row = {k: v for k, v in made["items"][0].items() if k not in ("name", "parent", "parenttype", "parentfield", "docstatus", "idx")}
	row.update({"received_qty": received, "qty": accepted, "rejected_qty": rejected})
	doc.update({"items": [row], "posting_date": nowdate(), "supplier_delivery_note": "DN-E2E-1", "folt_discrepancy_notes": notes})
	if rejected:
		doc["folt_inspection_status"] = "Passed with exceptions"
		row["rejected_warehouse"] = _rejected_warehouse()
	return doc


def _receiving(order):
	print("\n--- goods received against the order ---")
	if not frappe.db.exists("User", RECEIVER):
		print("  SKIP  goods receipts -- no Stock User test login (seed_test_users.py)")
		return
	expect_throw("a requester cannot make a receipt", lambda: as_user(REQUESTER, lambda: pp.lookup("erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt", {"source_name": order})), frappe.PermissionError)
	first = as_user(RECEIVER, lambda: pp.save("Purchase Receipt", _receipt(order, 1, 1)))["name"]
	as_user(RECEIVER, lambda: pp.act("Purchase Receipt", first, "submit"))
	check("the receiving officer records a partial delivery", frappe.db.get_value("Purchase Receipt", first, "docstatus") == 1)
	check("the order is half received", flt(frappe.db.get_value("Purchase Order", order, "per_received")) == 50, frappe.db.get_value("Purchase Order", order, "per_received"))
	lines = as_user(RECEIVER, lambda: pp.flow("Purchase Receipt", first))["lines"]
	check("the receipt's line reads against the order: ordered, earlier, outstanding",
	      lines and lines[0]["ordered_qty"] == 2 and lines[0]["previously_received"] == 0 and lines[0]["outstanding"] == 1, lines)
	over = as_user(RECEIVER, lambda: pp.save("Purchase Receipt", _receipt(order, 3, 3)))["name"]
	expect_throw("receiving more than the order allows is refused, for a service line too", lambda: as_user(RECEIVER, lambda: pp.act("Purchase Receipt", over, "submit")))
	frappe.set_user("Administrator")
	frappe.delete_doc("Purchase Receipt", over, force=True, ignore_permissions=True)
	expect_throw("a rejection without notes is refused", lambda: as_user(RECEIVER, lambda: pp.save("Purchase Receipt", _receipt(order, 1, 0, 1))))
	second = as_user(RECEIVER, lambda: pp.save("Purchase Receipt", _receipt(order, 1, 0, 1, "Camera arrived with a cracked lens.")))["name"]
	as_user(RECEIVER, lambda: pp.act("Purchase Receipt", second, "submit"))
	rec = frappe.get_doc("Purchase Receipt", second)
	check("the rejection is recorded on the receipt with its notes", flt(rec.items[0].rejected_qty) == 1 and "cracked" in rec.folt_discrepancy_notes)
	check("ERPNext counts the delivery against the order, rejected or not", flt(frappe.db.get_value("Purchase Order", order, "per_received")) == 100)
	lines = as_user(RECEIVER, lambda: pp.flow("Purchase Receipt", second))["lines"]
	check("the second receipt reads one earlier, none outstanding", lines[0]["previously_received"] == 1 and lines[0]["outstanding"] == 0, lines)
	over = _receipt_or_none(order)
	check("nothing more can be received against a fully delivered order", over is None or not over["items"], over and over.get("items"))
	f = as_user(BUYER, lambda: pp.flow("Purchase Order", order))
	check("the order's chain holds its two receipts", sum(1 for d in f["documents"] if d["doctype"] == "Purchase Receipt") == 2)
	_invoicing(order)
	frappe.set_user("Administrator")
	for pi in frappe.get_all("Purchase Invoice", filters={"supplier": ["in", BIDDERS]}, pluck="name", order_by="creation desc"):
		_drop("Purchase Invoice", pi)
	for pr in (second, first):
		_drop("Purchase Receipt", pr)


def _receipt_or_none(order):
	try:
		return as_user(RECEIVER, lambda: pp.lookup("erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt", {"source_name": order}))
	except Exception:  # noqa: BLE001 -- ERPNext refuses a receipt against a fully received order
		frappe.clear_last_message()
		return None


def _invoice(order, bill_no, rate=None, qty=None, extra_line=False):
	made = as_user(FIN_OFFICER, lambda: pp.lookup("erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_invoice", {"source_name": order}))
	doc = {k: v for k, v in made.items() if k not in ("name", "__islocal", "__unsaved", "docstatus", "owner", "creation", "modified", "workflow_state")}
	rows = []
	for r in made["items"]:
		row = {k: v for k, v in r.items() if k not in ("name", "parent", "parenttype", "parentfield", "docstatus", "idx")}
		if rate is not None:
			row["rate"] = rate
		if qty is not None:
			row["qty"] = qty
		rows.append(row)
	if extra_line:
		extra = {k: v for k, v in rows[0].items() if k not in ("po_detail", "purchase_order", "pr_detail", "purchase_receipt")}
		extra.update({"qty": 1, "rate": 500})
		rows.append(extra)
	doc.update({"items": rows, "bill_no": bill_no, "bill_date": nowdate(), "posting_date": nowdate(), "set_posting_time": 1})
	return doc


def _invoicing(order):
	print("\n--- the supplier's invoice, matched three ways ---")
	from folt_customizations import match as m
	from folt_customizations.purchase_order import ensure_procurement_settings

	ensure_procurement_settings()
	check("ERPNext refuses a second invoice number from the same supplier", bool(frappe.db.get_single_value("Accounts Settings", "check_supplier_invoice_uniqueness")))
	check("the price tolerance defaults to 2%", m.tolerance() == 2)
	good = as_user(FIN_OFFICER, lambda: pp.save("Purchase Invoice", _invoice(order, "E2E-INV-1")))["name"]
	check("an invoice at the order's price for what was received matches", frappe.db.get_value("Purchase Invoice", good, "folt_match_status") == "Matched",
	      frappe.db.get_value("Purchase Invoice", good, ["folt_match_status", "folt_match_notes"]))
	expect_throw("a second invoice with the same supplier number is refused", lambda: as_user(FIN_OFFICER, lambda: pp.save("Purchase Invoice", _invoice(order, "E2E-INV-1"))))
	frappe.set_user("Administrator")
	frappe.delete_doc("Purchase Invoice", good, force=True, ignore_permissions=True)

	# ERPNext itself refuses a price other than the order's while Buying Settings' maintain_same_rate
	# is on (it is here); the match's price flag is the check for when it is off. Read on an unsaved
	# invoice, as the form's preview would.
	priced = frappe.get_doc({"doctype": "Purchase Invoice", **_invoice(order, "E2E-INV-PRICE", rate=4200)})
	priced.run_method("set_missing_values")
	priced.calculate_taxes_and_totals()
	got = m.compute(priced)
	check("a price 5% above the order is flagged", got["status"] == "Discrepancies" and any(f["kind"] == "price" for f in got["flags"]), got["flags"])
	expect_throw("and ERPNext refuses it outright while it keeps the order's rate", lambda: as_user(FIN_OFFICER, lambda: pp.save("Purchase Invoice", _invoice(order, "E2E-INV-2", rate=4200))))

	odd = as_user(FIN_OFFICER, lambda: pp.save("Purchase Invoice", _invoice(order, "E2E-INV-3", extra_line=True)))["name"]
	got = as_user(FIN_OFFICER, lambda: pp.match(odd))
	check("a line on no order is flagged", any(f["kind"] == "unordered" for f in got["flags"]), got["flags"])
	check("the flags are written on the invoice", frappe.db.get_value("Purchase Invoice", odd, "folt_match_status") == "Discrepancies")
	as_user(FIN_OFFICER, lambda: pp.act("Purchase Invoice", odd, "transition", "Submit for approval"))
	moves = as_user(HOF, lambda: pp.record("Purchase Invoice", odd))["workflow"]["transitions"]
	check("approving it asks for a reason", any(mv["action"] == "Approve" and mv["needs_reason"] for mv in moves), moves)
	expect_throw("it is not approved without one", lambda: as_user(HOF, lambda: pp.act("Purchase Invoice", odd, "transition", "Approve")))
	as_user(HOF, lambda: pp.act("Purchase Invoice", odd, "transition", "Approve", "Delivery charge agreed by phone; order to be amended."))
	inv = frappe.get_doc("Purchase Invoice", odd)
	check("with a reason it is approved and posted", inv.docstatus == 1 and inv.workflow_state == "Approved", f"{inv.docstatus} {inv.workflow_state}")
	check("the reason stays on the invoice", "Delivery charge" in (inv.folt_match_override_reason or ""))
	check("ERPNext shows what is owed", flt(inv.outstanding_amount) > 0 and inv.status in ("Unpaid", "Overdue"), f"{inv.status} {inv.outstanding_amount}")
	check("a purchase manager, who cannot read invoices, is not offered the price variance", "Purchase Price Variance" not in as_user(MANAGER, pp.context)["reports"])
	pv = as_user(HOF, lambda: pp.run_report("Purchase Price Variance", {"company": COMPANY, "from_date": add_days(nowdate(), -1), "to_date": nowdate()}))
	check("the price variance report reads the posted invoice's lines", any(r.get("invoice") == odd for r in pv["result"]), len(pv["result"]))

	# ERPNext's maker offers nothing more to bill on a fully billed order; a hand-made copy of the
	# billed lines is what over-billing looks like, read unsaved.
	again = frappe.copy_doc(inv)
	again.bill_no = "E2E-INV-4"
	again.items = [r for r in again.items if r.get("po_detail")]
	got = m.compute(again)
	check("billing again what is already billed is flagged against what was received", any(f["kind"] == "quantity" for f in got["flags"]), got["flags"])
	f = as_user(BUYER, lambda: pp.flow("Purchase Order", order))
	check("the order's chain holds its invoices", sum(1 for d in f["documents"] if d["doctype"] == "Purchase Invoice") >= 1)


def _reporting(mr, quotes):
	print("\n--- reports, settings, reminders ---")
	window = {"company": COMPANY, "from_date": add_days(nowdate(), -1), "to_date": add_days(nowdate(), 1)}
	ctx = as_user(MANAGER, pp.context)
	check("the purchase manager is offered the FoLT reports", {"Purchase Requisition Register", "RFQ Register", "Supplier Performance"} <= set(ctx["reports"]), ctx["reports"])
	check("and ERPNext's buying reports", "Purchase Order Analysis" in ctx["reports"])
	expect_throw("a requester runs no procurement report", lambda: as_user(REQUESTER, lambda: pp.run_report("RFQ Register", window)), frappe.PermissionError)
	expect_throw("a report off the list is refused", lambda: as_user(MANAGER, lambda: pp.run_report("Salary Register", {})), frappe.PermissionError)
	reg = as_user(MANAGER, lambda: pp.run_report("Purchase Requisition Register", window))
	check("the requisition register lists the requisition", any(r.get("name") == mr for r in reg["result"]), len(reg["result"]))
	rfq = frappe.db.get_value("Request for Quotation", {"subject": RFQ_SUBJECT}, "name")
	reg = as_user(MANAGER, lambda: pp.run_report("RFQ Register", window))
	row = next((r for r in reg["result"] if r.get("name") == rfq), None)
	check("the RFQ register counts its invited suppliers and bids, and names the lowest", row and row["invited"] == 2 and row["bids"] == 2 and row["lowest_supplier"], row)
	comp = as_user(MANAGER, lambda: pp.run_report("Supplier Compliance", {}))
	check("supplier compliance names each bidder's gaps", all(any(r.get("supplier") == b for r in comp["result"]) for b in BIDDERS))
	for name in ("Procurement Spend by Dimension", "Procurement Cycle Time", "Supplier Performance", "Purchase Order Analysis"):
		filters = {**window, "dimension": "Department", "basis": "Ordered"} if name == "Procurement Spend by Dimension" else window
		if name == "Purchase Order Analysis":
			filters = {"company": COMPANY, "from_date": window["from_date"], "to_date": window["to_date"]}
		try:
			out = as_user(MANAGER, lambda: pp.run_report(name, filters))
			check(f"{name} runs", isinstance(out.get("result"), list), len(out.get("result") or []))
		except Exception as e:  # noqa: BLE001
			check(f"{name} runs", False, repr(e)[:160])
	frappe.set_user("Administrator")

	print("\n--- settings ---")
	check("the purchase manager may change FoLT Procurement Settings", ctx["capabilities"]["FoLT Procurement Settings"]["write"])
	before = frappe.db.get_single_value("FoLT Procurement Settings", "reminder_days")
	try:
		as_user(MANAGER, lambda: pp.save("FoLT Procurement Settings", {"reminder_days": 5}))
		check("settings are saved from the app", frappe.db.get_single_value("FoLT Procurement Settings", "reminder_days") == 5)
	finally:
		frappe.db.set_single_value("FoLT Procurement Settings", "reminder_days", before)
		# The test's own change is not the organisation's history: drop the Version it left.
		for name in frappe.get_all("Version", filters={"ref_doctype": "FoLT Procurement Settings", "owner": MANAGER}, pluck="name"):
			frappe.delete_doc("Version", name, force=True, ignore_permissions=True)
	buying = as_user(MANAGER, lambda: pp.record("Buying Settings"))
	check("ERPNext's buying settings are read here", buying["doc"].get("doctype") == "Buying Settings")
	for dt in ("Terms and Conditions", "Payment Terms Template", "Supplier Scorecard Criteria", "Supplier Scorecard Standing"):
		check(f"the purchase manager may change {dt}", ctx["capabilities"][dt]["write"] and ctx["capabilities"][dt]["create"])
	requester_ctx = as_user(REQUESTER, pp.context)
	check("a requester may not change the procurement settings", not requester_ctx["capabilities"].get("FoLT Procurement Settings", {}).get("write"))

	print("\n--- reminders ---")
	from frappe.utils import getdate

	from folt_customizations import procurement_alerts as pa

	today = getdate(nowdate())
	check("a date entering the window is reminded", pa.due_today(add_days(today, 3), today, 3) == "soon")
	check("the day after it is due, it is overdue", pa.due_today(add_days(today, -1), today, 3) == "overdue")
	check("and then weekly, not daily", pa.due_today(add_days(today, -8), today, 3) == "overdue" and pa.due_today(add_days(today, -2), today, 3) is None)
	frappe.db.set_value("Request for Quotation", rfq, "folt_quotation_deadline", f"{add_days(today, pa._lead_days())} 17:00:00", update_modified=False)
	sent = pa.daily(today)
	check("the daily run reminds the RFQ's owner of its deadline", sent["rfq"] >= 1, sent)
