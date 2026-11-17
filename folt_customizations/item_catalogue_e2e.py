"""End-to-end check of the items and services a requisition asks for, and where they are kept.

  * A requester adding a line to a purchase requisition finds items and services by name, code,
    group or description, and sees each by its name -- the code beside it, its group and unit in the
    hint -- never a disabled item, a sales-only one or a variant template. They may pick its unit.
  * The catalogue is kept under Procurement > Settings > Items and services: the Purchase Manager
    adds an item or service, an item group and a unit there; a Purchase User reads them; a
    requester reaches none of the three as a list.

Uses the test logins from seed_test_users.py and leaves nothing behind. Run with

    bench --site <site> execute folt_customizations.item_catalogue_e2e.run
"""

import frappe

from folt_customizations import procurement_portal as pp

MARK = "E2E IC"
SERVICE = f"{MARK} Boat hire Kalokol"
DISABLED = f"{MARK} Retired outboard engine"
SALES_ONLY = f"{MARK} Fish sold at market"
GROUP = f"{MARK} Field services"
UNIT = f"{MARK} Boat-day"

REQUESTER = "requester.test@folt.test"  # Employee
BUYER = "purchaser.test@folt.test"  # Purchase User
MANAGER = "procmanager.test@folt.test"  # Purchase Manager

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


def refused(user, fn):
	try:
		as_user(user, fn)
	except frappe.PermissionError:
		return True
	except Exception:
		return False
	return False


def teardown():
	frappe.set_user("Administrator")
	for name in frappe.get_all("Item", filters={"item_code": ["like", f"{MARK}%"]}, pluck="name"):
		frappe.delete_doc("Item", name, force=True, ignore_permissions=True)
	if frappe.db.exists("UOM", UNIT):
		frappe.delete_doc("UOM", UNIT, force=True, ignore_permissions=True)
	if frappe.db.exists("Item Group", GROUP):
		frappe.delete_doc("Item Group", GROUP, force=True, ignore_permissions=True)
	frappe.db.commit()


def _item(code, **extra):
	doc = frappe.get_doc({
		"doctype": "Item", "item_code": code, "item_name": code, "item_group": "Services", "stock_uom": "Nos",
		"is_stock_item": 0, "is_purchase_item": 1, **extra,
	})
	doc.insert(ignore_permissions=True)
	return doc.name


def run():
	PASS.clear()
	FAIL.clear()
	print("\nFoLT procurement — items and services on a requisition, and where they are kept\n")
	teardown()
	missing = [u for u in (REQUESTER, BUYER, MANAGER) if not frappe.db.exists("User", u)]
	if missing:
		frappe.throw(f"Run seed_test_users.py first: missing {', '.join(missing)}")
	try:
		picking()
		keeping()
	finally:
		frappe.set_user("Administrator")
		teardown()
	print(f"\n  {len(PASS)} passed, {len(FAIL)} failed")
	for label in FAIL:
		print(f"    FAIL  {label}")
	return {"passed": len(PASS), "failed": len(FAIL), "failures": FAIL}


def picking():
	print("Adding a line to a requisition")
	_item(SERVICE, item_name="Boat hire - Kalokol landing", description="<p>Motor boat with <b>coxswain</b> and fuel, per day, for the fish-landing survey.</p>")
	_item(DISABLED, disabled=1)
	_item(SALES_ONLY, is_purchase_item=0, is_sales_item=1)
	frappe.db.commit()

	def search(txt, **kw):
		return as_user(REQUESTER, lambda: pp.link_search("Item", txt, **kw))

	found = search(MARK)
	byval = {o["value"]: o for o in found}
	check("a requester finds an item or service by its code", SERVICE in byval, [o["value"] for o in found])
	option = byval.get(SERVICE) or {}
	check("…and sees it by its name, not its code", option.get("label") == "Boat hire - Kalokol landing", option.get("label"))
	check("…with its group and unit in the hint", (option.get("description") or "").startswith("Services · per Nos"), option.get("description"))
	check("…and its description, as plain words", "Motor boat with coxswain and fuel" in (option.get("description") or "") and "<" not in (option.get("description") or ""), option.get("description"))
	check("a disabled item is not offered", DISABLED not in byval)
	check("an item that is not bought is not offered", SALES_ONLY not in byval)
	check("found by a word of its name", SERVICE in [o["value"] for o in search("Kalokol landing")])
	check("found by a word of its description", SERVICE in [o["value"] for o in search("coxswain")])
	check("found by its group", any(o["value"] == SERVICE for o in search("Services", page_length=50)))
	check("the form's own filters still narrow it", SERVICE not in [o["value"] for o in search(MARK, filters={"item_group": "Products"})])
	check("…and cannot bring back a disabled item", DISABLED not in [o["value"] for o in search(MARK, filters={"disabled": 1})])
	everything = search("")
	labels = [o["label"] for o in everything]
	check("an empty search lists what can be bought, in name order", labels and labels == sorted(labels, key=str.lower), labels[:5])
	try:
		units = as_user(REQUESTER, lambda: pp.link_search("UOM", "Nos"))
		check("a requester may pick a line's unit", any(u["value"] == "Nos" for u in units), units[:3])
	except frappe.PermissionError as e:
		check("a requester may pick a line's unit", False, e)
	check("a requester does not open the catalogue as a list", refused(REQUESTER, lambda: pp.records("Item")))


def keeping():
	print("\nKeeping the catalogue in Procurement > Settings")
	caps = as_user(MANAGER, pp.context)["capabilities"]
	for doctype in ("Item", "Item Group", "UOM"):
		check(f"the Purchase Manager may add {doctype}", (caps.get(doctype) or {}).get("create") is True, caps.get(doctype))
	buyer_caps = as_user(BUYER, pp.context)["capabilities"]
	check("a Purchase User reads items and services", (buyer_caps.get("Item") or {}).get("read") is True, buyer_caps.get("Item"))
	check("…but does not change them", (buyer_caps.get("Item") or {}).get("write") is False, buyer_caps.get("Item"))

	group = as_user(MANAGER, lambda: pp.save("Item Group", {"item_group_name": GROUP, "parent_item_group": "All Item Groups"}))
	check("the Purchase Manager adds an item group", frappe.db.exists("Item Group", GROUP), group)
	unit = as_user(MANAGER, lambda: pp.save("UOM", {"uom_name": UNIT, "must_be_whole_number": 1}))
	check("…and a unit", frappe.db.exists("UOM", UNIT), unit)
	code = f"{MARK} Fuel bowser hire"
	saved = as_user(MANAGER, lambda: pp.save("Item", {
		"item_code": code, "item_name": "Fuel bowser hire", "item_group": GROUP, "stock_uom": UNIT,
		"is_stock_item": 0, "description": "Fuel bowser with driver",
	}))
	check("…and a service in them", frappe.db.exists("Item", code), saved)
	row = frappe.db.get_value("Item", code, ["is_purchase_item", "disabled", "item_group", "stock_uom"], as_dict=True) or {}
	check("a new item is buyable by default", row.get("is_purchase_item") == 1 and row.get("disabled") == 0, row)
	frappe.db.commit()
	offered = as_user(REQUESTER, lambda: pp.link_search("Item", "bowser"))
	check("a requester finds it on their next line straight away", [o["label"] for o in offered] == ["Fuel bowser hire"], offered)
	check("…counted in its own unit", f"per {UNIT}" in (offered[0]["description"] if offered else ""), offered)
	listed = as_user(BUYER, lambda: pp.records("Item", q="bowser"))
	check("a Purchase User finds it in Items and services", [r.name for r in listed["rows"]] == [code], listed["rows"])
	check("a Purchase User cannot change it", refused(BUYER, lambda: pp.save("Item", {"item_name": "Renamed"}, name=code)))
	check("a requester cannot add one", refused(REQUESTER, lambda: pp.save("Item", {"item_code": f"{MARK} Sneaked", "item_group": "Services", "stock_uom": "Nos"})))
	as_user(MANAGER, lambda: pp.save("Item", {"disabled": 1}, name=code))
	frappe.db.commit()
	check("disabling it in Settings takes it off the picker", not as_user(REQUESTER, lambda: pp.link_search("Item", "bowser")))
