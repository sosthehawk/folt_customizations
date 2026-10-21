"""End-to-end check of supplier_portal's two reads for the FoLT app: portal_overview and portal_rfq.

What this proves, in the order it would otherwise fail quietly:

  - both are whitelisted for GET only, and neither answers a Guest
  - a login that is nobody's portal user (staff included) gets empty lists, not an error
  - the supplier login sees exactly the RFQs sent to its suppliers -- checked against the RFQ
    Supplier rows directly -- and never a field about another bidder
  - an RFQ the supplier was not sent is refused, as on the portal page
  - pricing round-trips: save_quotation writes the draft, portal_rfq reads the same prices back,
    the overview says "draft" for that RFQ, and saving again revises the one draft instead of
    adding a second

It uses the local test login supplier@test.com (a portal user of Lodwar Taxi Services) and
needs an RFQ sent to that supplier. Everything it writes is rolled back. Run with:

    bench --site <site> execute folt_customizations.supplier_portal_e2e.run
"""

import frappe

from folt_customizations import supplier_portal

SUPPLIER_LOGIN = "supplier@test.com"
STAFF = "purchaser.test@folt.test"

PASS: list[str] = []
FAIL: list[str] = []

RFQ_KEYS = {"name", "title", "transaction_date", "schedule_date", "supplier", "lines", "status", "quotation", "total", "currency"}


def check(label, condition, detail=""):
	(PASS if condition else FAIL).append(label)
	print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")


def as_user(user, fn):
	previous = frappe.session.user
	frappe.set_user(user)
	frappe.local.message_log = []
	try:
		return fn()
	finally:
		frappe.set_user(previous)


def refused(fn) -> bool:
	try:
		fn()
	except frappe.PermissionError:
		return True
	return False


def run():
	frappe.set_user("Administrator")
	frappe.flags.mute_emails = True
	try:
		_run()
	finally:
		frappe.db.rollback()
		frappe.set_user("Administrator")

	print(f"\nRESULT: {len(PASS)} passed, {len(FAIL)} failed")
	if FAIL:
		for label in FAIL:
			print(f"  FAILED: {label}")
		raise SystemExit(1)


def _run():
	print("\n--- 0  the surface ---")
	for fn in (supplier_portal.portal_overview, supplier_portal.portal_rfq):
		check(
			f"{fn.__name__} is whitelisted for GET only, not for Guests",
			frappe.allowed_http_methods_for_whitelisted_func.get(fn) == ["GET"] and fn not in frappe.guest_methods,
			str(frappe.allowed_http_methods_for_whitelisted_func.get(fn)),
		)
	if not frappe.db.exists("User", SUPPLIER_LOGIN):
		check(f"{SUPPLIER_LOGIN} exists", False, "create it as a portal user of a supplier first")
		return
	mine = supplier_portal.my_suppliers(SUPPLIER_LOGIN)
	check(f"{SUPPLIER_LOGIN} is a portal user of at least one supplier", bool(mine), str(mine))

	print("\n--- 1  nobody's portal user ---")
	empty = as_user(STAFF, supplier_portal.portal_overview)
	check("a staff login gets empty lists, not an error", empty == {"suppliers": [], "rfqs": [], "quotations": [], "orders": []}, str(empty)[:120])

	print("\n--- 2  the supplier's overview ---")
	overview = as_user(SUPPLIER_LOGIN, supplier_portal.portal_overview)
	check("its suppliers are exactly its portal-user suppliers", sorted(s["name"] for s in overview["suppliers"]) == sorted(mine))
	expected = set(
		frappe.db.sql_list(
			"""select distinct rfq.name from `tabRequest for Quotation` rfq
			join `tabRequest for Quotation Supplier` s on s.parent = rfq.name
			where rfq.docstatus = 1 and s.supplier in %(mine)s""",
			{"mine": mine},
		)
	)
	shown = {r["name"] for r in overview["rfqs"]}
	check("the RFQs listed are exactly the submitted ones sent to its suppliers", shown == expected or (len(shown) == supplier_portal.OVERVIEW_LIMIT and shown <= expected), f"{len(shown)} vs {len(expected)}")
	check("every RFQ row carries only the supplier's own fields", all(set(r) == RFQ_KEYS for r in overview["rfqs"]))
	check("every row is for one of its suppliers", all(r["supplier"] in mine for r in overview["rfqs"]))
	check("every quotation and order is its own", all(q["supplier"] in mine for q in overview["quotations"]) and all(o["supplier"] in mine for o in overview["orders"]))

	print("\n--- 3  one RFQ ---")
	elsewhere = frappe.db.sql_list(
		"""select rfq.name from `tabRequest for Quotation` rfq where rfq.docstatus = 1 and not exists (
			select 1 from `tabRequest for Quotation Supplier` s where s.parent = rfq.name and s.supplier in %(mine)s) limit 1""",
		{"mine": mine},
	)
	if elsewhere:
		check("an RFQ it was not sent is refused", as_user(SUPPLIER_LOGIN, lambda: refused(lambda: supplier_portal.portal_rfq(elsewhere[0]))))
	open_rfq = next((r for r in overview["rfqs"] if r["status"] != "submitted"), None)
	if not open_rfq:
		check("an RFQ it can still price exists", False, "send one to its supplier first")
		return
	view = as_user(SUPPLIER_LOGIN, lambda: supplier_portal.portal_rfq(open_rfq["name"]))
	lines = frappe.get_all("Request for Quotation Item", filters={"parent": open_rfq["name"]}, pluck="name", order_by="idx asc")
	check("its lines are the RFQ's, in order", [i["request_for_quotation_item"] for i in view["items"]] == lines)
	check("it is editable and its status agrees with the overview", view["editable"] and view["status"] == open_rfq["status"])

	print("\n--- 4  pricing round-trips ---")
	prices = [{"request_for_quotation_item": line, "rate": 100 + i} for i, line in enumerate(lines)]
	first = as_user(SUPPLIER_LOGIN, lambda: supplier_portal.save_quotation(open_rfq["name"], frappe.as_json(prices), "Valid 30 days"))
	again = as_user(SUPPLIER_LOGIN, lambda: supplier_portal.portal_rfq(open_rfq["name"]))
	check("the saved prices come back on the same lines", [i["rate"] for i in again["items"]] == [p["rate"] for p in prices], str([i["rate"] for i in again["items"]]))
	check("the RFQ now reads as a draft, with the supplier's terms", again["status"] == "draft" and again["quotation"] == first and again["terms"] == "Valid 30 days")
	second = as_user(SUPPLIER_LOGIN, lambda: supplier_portal.save_quotation(open_rfq["name"], frappe.as_json(prices), None))
	check("saving again revises the same draft", second == first, f"{first} then {second}")
	row = next(r for r in as_user(SUPPLIER_LOGIN, supplier_portal.portal_overview)["rfqs"] if r["name"] == open_rfq["name"])
	check("the overview says draft, with the draft's total", row["status"] == "draft" and row["quotation"] == first and row["total"] == sum(p["rate"] * q for p, q in zip(prices, [i["qty"] for i in view["items"]])), str(row))
