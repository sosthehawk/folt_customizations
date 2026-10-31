"""The location tree: Kenya, its 47 counties, and FoLT's Turkana places beneath them.

FoLT Location became a tree (Country > County > Sub-county > Constituency > Ward > Location >
Community > Village > Site) without changing a single record's name, because it is an Accounting
Dimension and the ledger links by name. apply_location_tree, run on every migrate, is idempotent:

- Kenya and its 47 counties (with their official county codes, 001-047) exist as groups;
- a location whose name is some other location's `parent_region` is that region's node -- FoLT's
  six Turkana sub-counties were already rows like this -- and becomes a Sub-county group under
  Turkana County;
- every location still without a parent goes under its `parent_region` node.

Sub-counties beyond Turkana's, constituencies and wards are entered or imported by people; only the
counties are seeded, because they are the one level fixed by the Constitution.
"""

import frappe
from frappe.utils.nestedset import rebuild_tree

DOCTYPE = "FoLT Location"
PARENT = "parent_folt_location"
COUNTRY = "Kenya"
FOLT_COUNTY = "Turkana County"

# First Schedule to the Constitution of Kenya, in order: the county code is the position.
COUNTIES = (
	"Mombasa", "Kwale", "Kilifi", "Tana River", "Lamu", "Taita Taveta", "Garissa", "Wajir", "Mandera",
	"Marsabit", "Isiolo", "Meru", "Tharaka Nithi", "Embu", "Kitui", "Machakos", "Makueni", "Nyandarua",
	"Nyeri", "Kirinyaga", "Murang'a", "Kiambu", "Turkana", "West Pokot", "Samburu", "Trans Nzoia",
	"Uasin Gishu", "Elgeyo Marakwet", "Nandi", "Baringo", "Laikipia", "Nakuru", "Narok", "Kajiado",
	"Kericho", "Bomet", "Kakamega", "Vihiga", "Bungoma", "Busia", "Siaya", "Kisumu", "Homa Bay",
	"Migori", "Kisii", "Nyamira", "Nairobi City",
)


def _ensure(name, level, parent, code):
	if frappe.db.exists(DOCTYPE, name):
		return
	frappe.get_doc(
		{"doctype": DOCTYPE, "location_name": name, "location_level": level, PARENT: parent, "is_group": 1, "location_code": code}
	).insert(ignore_permissions=True)


def apply_location_tree():
	if not frappe.get_meta(DOCTYPE).has_field(PARENT):
		return
	_ensure(COUNTRY, "Country", None, "KE")
	for i, county in enumerate(COUNTIES, start=1):
		_ensure(f"{county} County", "County", COUNTRY, f"KE-{i:03d}")

	regions = {r for r in frappe.get_all(DOCTYPE, filters={"parent_region": ["is", "set"]}, pluck="parent_region")}
	for region in regions:
		if not frappe.db.exists(DOCTYPE, region):
			continue
		row = frappe.db.get_value(DOCTYPE, region, [PARENT, "is_group", "location_level"], as_dict=True)
		updates = {}
		if not row.get(PARENT):
			updates[PARENT] = FOLT_COUNTY
		if not row.is_group:
			updates["is_group"] = 1
		if not row.location_level:
			updates["location_level"] = "Sub-county"
		if updates:
			frappe.db.set_value(DOCTYPE, region, updates, update_modified=False)

	for name, region in frappe.get_all(
		DOCTYPE, filters={PARENT: ["is", "not set"], "parent_region": ["is", "set"]}, fields=["name", "parent_region"], as_list=True
	):
		if name != region and frappe.db.exists(DOCTYPE, region):
			frappe.db.set_value(DOCTYPE, name, PARENT, region, update_modified=False)

	rebuild_tree(DOCTYPE)
