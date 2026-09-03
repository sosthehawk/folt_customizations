"""End-to-end check on the renamed Desk tiles surviving a saved /desk layout.

WHAT THIS DEFENDS, and why it is not visible from the code that does the renaming.

branding.DESKTOP_ICON_LABELS renames the Desktop Icon rows -- "Framework" -> "System Admin",
"ERPNext Settings" -> "FoLT Settings" -- and for months the rows on staging were correct while
/desk went on saying "Framework" and "ERPNext Settings" to the person looking at it. Nothing was
wrong with the rename. The page was not reading it.

frappe v16 keeps the /desk grid per user as a **Desktop Layout**: one JSON snapshot of the icon
rows, written the first time that user drags a tile, makes a folder or hides one. The page hands
it to the client (desk/page/desktop/desktop.py) and desktop.js:sync_layout() prefers it outright
over frappe.boot.desktop_icons -- which is the live rows. So a user who has ever rearranged their
Desk is pinned to the labels, logos and folder membership of the day they did it, and no amount
of re-applying the rename to the Desktop Icon table reaches them.

branding._refresh_saved_layouts() rewrites those snapshots in place. This file checks the three
things that make it work, none of which show up in a screenshot of a working Desk:

  - THE PREMISE, in frappe's own source. That the saved layout wins over boot.desktop_icons is
    the entire reason this code exists. If a frappe release drops Desktop Layout, or reverses
    that preference, the rewrite becomes dead weight -- and, worse, the reasoning in the
    comments becomes false while everything still looks fine. Both claims are re-derived from
    the files on disk rather than trusted.

  - THE ROUND TRIP. A snapshot carrying the pre-rename wording is built by hand, put in front of
    the real function, and read back: the labels, the logo, the link target and the folder
    membership must all come back FoLT's. Nothing else in the row may move -- the tile order,
    the hidden flags and the folders the user made are their decisions, not ours.

  - IDEMPOTENCE. This runs on every migrate. A second pass must write nothing at all, or every
    migrate dirties every user's layout and the "did anything change" signal that gates
    frappe.clear_cache() stops meaning anything.

Uses Administrator's own Desktop Layout row as the fixture -- there is nowhere else to put one,
since the doctype is named after the user it belongs to -- and puts back exactly what it found,
including putting back nothing when there was nothing. Run with

    bench --site <site> execute folt_customizations.desk_layout_e2e.run
"""

import json
import os

import frappe

from folt_customizations import branding

PASS, FAIL = [], []

# The user whose layout row is borrowed for the round trip. Administrator, because it exists on
# every site and because a site where Administrator has arranged their Desk is the exact case
# this is about.
FIXTURE_USER = "Administrator"


def check(label, condition, detail=""):
	(PASS if condition else FAIL).append(label)
	print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")


def frappe_file(*parts) -> str:
	return os.path.join(frappe.get_app_path("frappe"), *parts)


def read(path) -> str:
	with open(path) as handle:
		return handle.read()


def check_the_premise():
	print("\n--- the premise: a saved layout beats the live rows ---")

	page = frappe_file("desk", "page", "desktop", "desktop.py")
	script = frappe_file("desk", "page", "desktop", "desktop.js")

	check("frappe still has a Desktop Layout doctype", bool(frappe.db.exists("DocType", "Desktop Layout")))

	if os.path.exists(page):
		source = read(page)
		check(
			"the /desk page hands the user's saved Desktop Layout to the template",
			'frappe.get_doc("Desktop Layout"' in source and "desktop_layout" in source,
		)
	else:
		check("desk/page/desktop/desktop.py is where it has always been", False, page)

	if os.path.exists(script):
		source = read(script)
		# The two branches of sync_layout(), in order. The saved layout is used when it has any
		# keys at all; boot.desktop_icons is the fallback, not the source of truth.
		check(
			"desktop.js prefers the saved layout over frappe.boot.desktop_icons",
			"frappe.desktop_icons = this.data" in source
			and "frappe.desktop_icons = frappe.boot.desktop_icons" in source,
		)
		check(
			"a tile renders the label carried in that layout row",
			"this.icon_title = this.icon_data.label" in source,
		)
		check(
			"a child whose parent label is missing falls out of the folder to the top level",
			"!icon.parent_icon || !icon_map[icon.parent_icon]" in source,
		)
	else:
		check("desk/page/desktop/desktop.js is where it has always been", False, script)


def check_the_live_rows():
	print("\n--- the live rows: the rename itself ---")

	for name, label in branding._renamed_labels().items():
		if not frappe.db.exists("Desktop Icon", name):
			check(f"Desktop Icon {name!r} exists to be renamed", False)
			continue
		check(
			f"Desktop Icon {name!r} is labelled {label!r}",
			frappe.db.get_value("Desktop Icon", name, "label") == label,
		)

	check(
		"the Workspace Sidebar the FoLT Settings tile routes to exists",
		bool(frappe.db.exists("Workspace Sidebar", "FoLT Settings")),
		"without it the tile dead-ends in 'Icon is not correctly configured'",
	)


def stale_layout():
	"""A saved layout as it looked before the renames: shipping labels, shipping parents.

	Built from the rename map rather than written out by hand, so an icon added to
	branding.DESKTOP_ICON_LABELS is exercised here without anybody remembering to come back --
	which is the failure this whole file is about, one level up.

	Each renamed icon gets a child carrying the OLD label as its `parent_icon`, because that is
	how a folder holds its contents (by the parent's label, never by its name) and it is what
	silently empties a folder when the parent is renamed alone. The child's own name is not a
	Desktop Icon at all: it stands in for every row in the layout that FoLT does not manage, and
	the assertions below require that its label comes back untouched.
	"""
	rows = []
	for name in sorted(branding._renamed_labels()):
		live = frappe.db.get_value(
			"Desktop Icon", name, ["icon_type", "app", "link_to"], as_dict=True
		) or frappe._dict()
		parent = {
			"name": name,
			"label": name,  # for a standard icon the docname IS the shipping label
			"icon_type": live.get("icon_type") or "App",
			"app": live.get("app"),
			"parent_icon": None,
			"logo_url": None,
			"link_to": name if live.get("link_to") else None,
			"idx": len(rows) + 1,
			"hidden": 0,
			"child_icons": [],
		}
		child = {
			"name": f"Not A FoLT Icon ({name})",
			"label": f"Not A FoLT Icon ({name})",
			"icon_type": "Link",
			"app": live.get("app"),
			"parent_icon": name,
			"logo_url": None,
			"link_to": "Home",
			"idx": len(rows) + 2,
			"hidden": 0,
			"child_icons": [],
		}
		# The client writes a copy of a folder's contents back inside the folder as well as in
		# the flat list, so the nested copy has to go stale too or the fixture is easier than
		# reality.
		parent["child_icons"] = [dict(child)]
		rows += [parent, child]
	return rows


def check_the_round_trip():
	print("\n--- the round trip: a stale snapshot, repaired ---")

	renames = branding._renamed_labels()
	existing = None
	if frappe.db.exists("Desktop Layout", FIXTURE_USER):
		existing = frappe.db.get_value("Desktop Layout", FIXTURE_USER, "layout")
	else:
		doc = frappe.new_doc("Desktop Layout")
		doc.user = FIXTURE_USER
		doc.flags.ignore_permissions = True
		doc.insert()

	try:
		frappe.db.set_value(
			"Desktop Layout", FIXTURE_USER, "layout", json.dumps(stale_layout()), update_modified=False
		)

		check("the stale snapshot was rewritten", branding._refresh_saved_layouts() is True)

		rows = {
			row["name"]: row
			for row in json.loads(frappe.db.get_value("Desktop Layout", FIXTURE_USER, "layout"))
		}

		for name, label in sorted(renames.items()):
			row = rows[name]
			check(f"the {name!r} tile now reads {label!r}", row["label"] == label, row["label"])

			live = frappe.db.get_value(
				"Desktop Icon", name, branding.LAYOUT_ICON_FIELDS, as_dict=True
			)
			if live:
				check(
					f"every icon field of {name!r} came back from the live row",
					all(
						row[field] == live[field]
						for field in branding.LAYOUT_ICON_FIELDS
						if live[field] is not None
					),
					", ".join(f"{f}={row[f]!r}" for f in branding.LAYOUT_ICON_FIELDS),
				)

			child = rows[f"Not A FoLT Icon ({name})"]
			check(
				f"the tile inside {name!r} still points at it, so the folder keeps it",
				child["parent_icon"] == label,
				child["parent_icon"],
			)
			check(
				f"the copy nested inside {name!r} was rewritten too",
				row["child_icons"][0]["parent_icon"] == label,
			)
			check(
				f"a row FoLT does not manage is left alone inside {name!r}",
				child["label"] == f"Not A FoLT Icon ({name})" and child["link_to"] == "Home",
			)

		check(
			"the two tiles this was reported for read as FoLT's",
			rows["Framework"]["label"] == "System Admin"
			and rows["ERPNext Settings"]["label"] == "FoLT Settings",
		)
		check(
			"the settings tile routes at the Workspace Sidebar FoLT ships",
			rows["ERPNext Settings"]["link_to"] == "FoLT Settings",
			"the label is the routing key for a Link icon; link_to has to follow it",
		)

		stale = {row["name"]: row for row in stale_layout()}
		check(
			"the user's own arrangement is untouched -- tile order and hidden flags",
			all(
				rows[name]["idx"] == row["idx"] and rows[name]["hidden"] == row["hidden"]
				for name, row in stale.items()
			),
		)

		check(
			"a second pass writes nothing, so every migrate is not a dirty write",
			branding._refresh_saved_layouts() is False,
		)
	finally:
		if existing is None:
			frappe.delete_doc("Desktop Layout", FIXTURE_USER, force=True, ignore_permissions=True)
		else:
			frappe.db.set_value(
				"Desktop Layout", FIXTURE_USER, "layout", existing, update_modified=False
			)
		frappe.db.commit()


def run():
	check_the_premise()
	check_the_live_rows()
	check_the_round_trip()

	print("\n" + "=" * 60)
	print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
	for label in FAIL:
		print(f"  FAILED: {label}")
	return {"passed": len(PASS), "failed": len(FAIL), "failures": FAIL}
