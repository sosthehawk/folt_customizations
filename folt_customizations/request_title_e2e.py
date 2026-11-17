"""End-to-end check of the request title (request_title.py) through the procurement edge.

A purchase is known by its request title from requisition to invoice, and found by its title, its
requisition reference, or the name or code of what it buys:

  * the requester's typed title is the requisition's (and ERPNext's own `title` follows it); left
    empty, it is made from the items;
  * ERPNext's makers carry it, and the requisition's reference, to the RFQ, the bid and the order;
    a document saved without them gets them on save; one a buyer retitled keeps its own;
  * the lists search the title, the reference, and each line's item name and code -- a document
    once however many lines match -- and a line match never shows a document the reader may not
    open.

Uses the test logins from seed_test_users.py and leaves nothing behind. Run with

    bench --site <site> execute folt_customizations.request_title_e2e.run
"""

import frappe
from frappe.utils import add_days, nowdate

from folt_customizations import procurement_portal as pp
from folt_customizations.procurement_e2e_support import JUSTIFICATION, approved_requisition, rfq_lines

COMPANY = "FRIENDS of LAKE TURKANA"
MARK = "E2E RT"
ITEM = "E2E RT Solar Lantern"
ITEM_CODE_FRAGMENT = "RT Solar Lant"
TITLE = f"{MARK} Solar lanterns for the Kalokol fish bandas"
SUPPLIER = "E2E RT Lantern Supplies"

REQUESTER = "requester.test@folt.test"  # Employee, Programs Department
BUYER = "procmanager.test@folt.test"

PASS, FAIL = [], []


def check(label, condition, detail=""):
	(PASS if condition else FAIL).append(label)
	print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  — ' + str(detail) if detail else ''}")


def as_user(user, fn):
	previous = frappe.session.user
	frappe.set_user(user)
	try:
		return fn()
	finally:
		frappe.set_user(previous)


def _names(doctype, q, user=BUYER):
	out = as_user(user, lambda: pp.records(doctype, q=q, page_length=100))
	return [r.name for r in out["rows"]], out["total"]


def _drop(doctype, name):
	doc = frappe.get_doc(doctype, name)
	if doc.docstatus == 1:
		doc.flags.ignore_permissions = True
		doc.cancel()
	frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)


def teardown():
	frappe.set_user("Administrator")
	for doctype in ("Purchase Order", "Supplier Quotation", "Request for Quotation"):
		for name in frappe.get_all(doctype, filters={"folt_request_title": ["like", f"%{MARK}%"]}, pluck="name", order_by="creation desc"):
			_drop(doctype, name)
	for name in frappe.get_all("Material Request", filters={"title": ["like", f"%{MARK}%"]}, pluck="name", order_by="creation desc"):
		_drop("Material Request", name)
	if frappe.db.exists("Supplier", SUPPLIER):
		frappe.delete_doc("Supplier", SUPPLIER, force=True, ignore_permissions=True)
	frappe.db.commit()


def setup():
	missing = [u for u in (REQUESTER, BUYER) if not frappe.db.exists("User", u)]
	if missing:
		frappe.throw(f"Run seed_test_users.py first: missing {', '.join(missing)}")
	if not frappe.db.exists("Item", ITEM):
		frappe.get_doc({"doctype": "Item", "item_code": ITEM, "item_name": ITEM, "item_group": "Products" if frappe.db.exists("Item Group", "Products") else "All Item Groups",
		                "stock_uom": "Nos", "is_stock_item": 0, "is_purchase_item": 1}).insert(ignore_permissions=True)
	group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
	frappe.get_doc({"doctype": "Supplier", "supplier_name": SUPPLIER, "supplier_group": group, "country": "Kenya"}).insert(ignore_permissions=True)
	frappe.db.commit()


def run():
	PASS.clear()
	FAIL.clear()
	print("\nFoLT procurement — the request title along the buying chain\n")
	teardown()
	setup()
	try:
		requisition_titles()
		carried()
	finally:
		frappe.set_user("Administrator")
		teardown()
	print(f"\n  {len(PASS)} passed, {len(FAIL)} failed")
	for label in FAIL:
		print(f"    FAIL  {label}")
	return {"passed": len(PASS), "failed": len(FAIL), "failures": FAIL}


def _requisition(title=None):
	doc = {
		"company": COMPANY,
		"schedule_date": add_days(nowdate(), 14),
		"folt_justification": JUSTIFICATION,
		"items": [{"item_code": ITEM, "qty": 4, "folt_estimated_rate": 2500, "schedule_date": add_days(nowdate(), 14)},
		          {"item_code": ITEM, "qty": 2, "folt_estimated_rate": 2500, "schedule_date": add_days(nowdate(), 14)}],
	}
	if title:
		doc["folt_request_title"] = title
	return as_user(REQUESTER, lambda: pp.save("Material Request", frappe.as_json(doc)))["name"]


def requisition_titles():
	print("The requisition")
	typed = _requisition(TITLE)
	row = frappe.db.get_value("Material Request", typed, ["folt_request_title", "title"], as_dict=True)
	check("the requester's typed title is the request title", row.folt_request_title == TITLE, row.folt_request_title)
	check("ERPNext's own title follows it, so the Desk shows the same words", row.title == TITLE, row.title)

	untyped = _requisition()
	row = frappe.db.get_value("Material Request", untyped, ["folt_request_title", "title"], as_dict=True)
	check("left empty, the request title is made from the items", ITEM in (row.folt_request_title or "") and row.title == row.folt_request_title, row.folt_request_title)
	# Mark it so teardown finds it (the derived title carries no mark).
	frappe.db.set_value("Material Request", untyped, "title", f"{MARK} untyped", update_modified=False)

	retitled = f"{TITLE} (revised)"
	as_user(REQUESTER, lambda: pp.save("Material Request", frappe.as_json({"folt_request_title": retitled,
	                                                                         "modified": str(frappe.db.get_value("Material Request", typed, "modified"))}), typed))
	row = frappe.db.get_value("Material Request", typed, ["folt_request_title", "title"], as_dict=True)
	check("an edited title is the title again on both fields", row.folt_request_title == retitled == row.title, row.title)

	print("\nFinding a requisition")
	names, _total = _names("Material Request", "Kalokol fish bandas")
	check("found by words of its title", typed in names, names)
	names, total = _names("Material Request", ITEM_CODE_FRAGMENT)
	check("found by its item's code or name", typed in names and untyped in names, names)
	check("once each, though two lines match", len(names) == len(set(names)) and total == len(names), total)
	names, _total = _names("Material Request", typed)
	check("found by its reference number", names == [typed], names)
	# An Employee with no procurement role (procurement_portal_e2e's: a seeded one, or one it makes
	# and its own teardown removes).
	from folt_customizations.procurement_portal_e2e import _other_employee

	frappe.set_user("Administrator")
	names, _total = _names("Material Request", ITEM_CODE_FRAGMENT, user=_other_employee())
	check("a line match never shows another employee's requisition", typed not in names and untyped not in names, names)
	names, _total = _names("Material Request", ITEM_CODE_FRAGMENT, user=REQUESTER)
	check("the requester finds their own by item", typed in names, names)


def carried():
	print("\nAlong the chain")
	mr = approved_requisition(COMPANY, ITEM, qty=3, rate=2500, title=f"{MARK} Lanterns for the Ileret clinic")
	check("a requisition given only ERPNext's title has it as its request title", mr.folt_request_title == mr.title, mr.folt_request_title)

	made = as_user(BUYER, lambda: pp.lookup("erpnext.stock.doctype.material_request.material_request.make_request_for_quotation", frappe.as_json({"source_name": mr.name})))
	check("the RFQ ERPNext makes from it shows the request title before it is saved", made.get("folt_request_title") == mr.folt_request_title, made.get("folt_request_title"))
	check("...and the requisition's reference", made.get("folt_request") == mr.name, made.get("folt_request"))

	# Saved the way REST or the Desk would, without either field: the save fills them.
	rfq = frappe.get_doc({
		"doctype": "Request for Quotation", "company": COMPANY, "transaction_date": nowdate(), "schedule_date": add_days(nowdate(), 14),
		"message_for_supplier": f"{MARK} please quote", "items": rfq_lines(mr), "suppliers": [{"supplier": SUPPLIER}],
	}).insert(ignore_permissions=True)
	check("an RFQ saved without them is given the title", rfq.folt_request_title == mr.folt_request_title, rfq.folt_request_title)
	check("...and the reference", rfq.folt_request == mr.name, rfq.folt_request)

	sq = frappe.get_doc({
		"doctype": "Supplier Quotation", "supplier": SUPPLIER, "company": COMPANY, "transaction_date": nowdate(),
		"items": [{"item_code": ITEM, "qty": 3, "rate": 2400, "uom": "Nos", "conversion_factor": 1,
		           "request_for_quotation": rfq.name, "request_for_quotation_item": rfq.items[0].name}],
	}).insert(ignore_permissions=True)
	check("a bid on the RFQ inherits through the RFQ", sq.folt_request_title == mr.folt_request_title and sq.folt_request == mr.name, (sq.folt_request_title, sq.folt_request))

	po = frappe.get_doc({
		"doctype": "Purchase Order", "supplier": SUPPLIER, "company": COMPANY, "transaction_date": nowdate(), "schedule_date": add_days(nowdate(), 14),
		"folt_request_title": f"{MARK} Lanterns, first tranche",
		"items": [{"item_code": ITEM, "qty": 3, "rate": 2400, "uom": "Nos", "conversion_factor": 1, "schedule_date": add_days(nowdate(), 14),
		           "material_request": mr.name, "material_request_item": mr.items[0].name}],
	})
	po.flags.ignore_permissions = True
	po.insert()
	check("an order a buyer titled keeps its own title", po.folt_request_title == f"{MARK} Lanterns, first tranche", po.folt_request_title)
	check("...and still gets the requisition's reference", po.folt_request == mr.name, po.folt_request)

	print("\nFinding the purchase downstream")
	names, _total = _names("Request for Quotation", mr.name)
	check("an RFQ is found by its requisition's reference", rfq.name in names, names)
	names, _total = _names("Request for Quotation", "Ileret clinic")
	check("...and by the request title", rfq.name in names, names)
	names, _total = _names("Supplier Quotation", ITEM_CODE_FRAGMENT)
	check("a bid is found by its item", sq.name in names, names)
	names, _total = _names("Purchase Order", "first tranche")
	check("an order is found by its title", po.name in names, names)

	flow = as_user(BUYER, lambda: pp.flow("Purchase Order", po.name))
	titles = {d["name"]: d.get("title") for d in flow["documents"]}
	check("the chain card names each document by its request title", titles.get(mr.name) == mr.folt_request_title and titles.get(rfq.name) == mr.folt_request_title, titles)
	row = frappe.db.get_value("Material Request", mr.name, "title")
	check("the requisition's codes and links are untouched", row == mr.title and frappe.db.get_value("Request for Quotation Item", rfq.items[0].name, "material_request") == mr.name)
