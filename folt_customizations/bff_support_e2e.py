"""End-to-end check of bff_support: the four reads the FoLT BFF asks Frappe for as the user.

What this proves, in the order it would otherwise fail quietly:

  - all four are whitelisted for GET only, and none answers a Guest
  - no call leaves a message on message_log for any role -- a message there ships to the BFF as
    `_server_messages` on a successful response and is shown as a notice
  - backups: refused to every role but System Manager; the grouping, ordering, limit and kinds
    are right against a directory of fakes (.tar and .tgz, -enc, partial, noise); and the site
    config is listed but NEVER linked and never reported as encrypted, live and in the fakes
  - my_holidays: nothing for a user with no Employee; for an employee, exactly the days HRMS
    itself would call holidays -- checked against get_holiday_list_for_employee asked day by day,
    across employee- and company-level assignments that start inside the range
  - my_leave_approvers: the mandatory flag is HR Settings', the candidates walk up the department
    tree nearest first, the user themself and disabled accounts are dropped, and the default
    follows the map's three rules
  - activity_feed: a user sees only moves on documents they can read (Head of Programs vs Purchase
    User), a comment on a deleted document is skipped without queueing "not found", workflow-less
    doctypes read as notes, a turn-down's two comments are one row, and only public, unexpired
    notes are announced
  - the Leave Application.folt_decision_reason fixture row is installed as the contract says

THIS SUITE DELIBERATELY DOES NOT SET frappe.flags.in_test, for the same reason spa_e2e does not.

Every fixture is made inside the run's transaction and rolled back, then torn down by name for
good measure; the two existing records it touches (the requester's Employee and one test user's
`enabled`) are put back explicitly. Idempotent. Run with:

    bench --site <site> execute folt_customizations.bff_support_e2e.run
"""

import json
import os
import re
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

import frappe
from frappe.utils import add_days, cint, get_first_day, getdate, now_datetime, nowdate
from hrms.utils.holiday_list import get_holiday_list_for_employee

from folt_customizations import bff_support, spa, step_forms

REQUESTER = "requester.test@folt.test"
HEAD_OF_PROGRAMS = "hop.test@folt.test"
HEAD_OF_FINANCE = "hof.test@folt.test"
FINANCE_OFFICER = "finofficer.test@folt.test"
FINANCE_ASSISTANT = "finassistant.test@folt.test"
EXECUTIVE_DIRECTOR = "ed.test@folt.test"
OPERATIONS = "oso.test@folt.test"
FINANCE_MANAGER = "finmanager.test@folt.test"
COMMITTEE = "committee.test@folt.test"
PURCHASER = "purchaser.test@folt.test"

USERS = (
	REQUESTER, HEAD_OF_PROGRAMS, HEAD_OF_FINANCE, FINANCE_OFFICER, FINANCE_ASSISTANT,
	EXECUTIVE_DIRECTOR, OPERATIONS, FINANCE_MANAGER, COMMITTEE, PURCHASER,
)

HOLIDAYS_A = "E2E BFF Holidays A"
HOLIDAYS_B = "E2E BFF Holidays B"
APPROVER_ROW = "e2e-bff-approver-"
COMMENT_ROW = "e2e-bff-comment-"
NOTE_TITLE = "E2E BFF "
GONE = "E2E-BFF-GONE-0001"
REASON = "E2E BFF the figures do not add up"

ITEM_KEYS = {"doctype", "name", "title", "state", "tone", "kind", "reason", "derived", "by", "by_name", "at"}
KINDS = {"forward", "turned_down", "derived", "finished", "note"}
TONES = {"ok", "warn", "danger", "info", "plain"}
STAMP = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")

PASS, FAIL = [], []


def check(label, condition, detail=""):
	(PASS if condition else FAIL).append(label)
	print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")


def refused(fn, exc=Exception) -> str | None:
	"""The refusal's text, or None if nothing was refused."""
	try:
		fn()
	except exc as e:  # noqa: BLE001
		# An empty message is still a refusal -- the type is the answer then (see spa_e2e).
		return str(e).replace("\n", " ")[:160] or type(e).__name__
	return None


def as_user(user, fn):
	previous = frappe.session.user
	frappe.set_user(user)
	frappe.local.message_log = []
	try:
		return fn()
	finally:
		frappe.set_user(previous)


def with_log(fn):
	"""fn's answer and whatever it left on message_log, read before as_user resets it."""
	return lambda: (fn(), list(frappe.local.message_log))


def require_users():
	missing = [user for user in USERS if not frappe.db.exists("User", user)]
	if missing:
		raise SystemExit(f"seed the role test users first (seed_test_users.py): missing {missing}")


def teardown():
	frappe.set_user("Administrator")
	for name in frappe.get_all(
		"Holiday List Assignment", filters={"holiday_list": ["in", [HOLIDAYS_A, HOLIDAYS_B]]}, pluck="name"
	):
		doc = frappe.get_doc("Holiday List Assignment", name)
		if doc.docstatus == 1:
			doc.flags.ignore_permissions = True
			doc.cancel()
		frappe.delete_doc("Holiday List Assignment", name, force=True, ignore_permissions=True)
	for name in (HOLIDAYS_A, HOLIDAYS_B):
		if frappe.db.exists("Holiday List", name):
			frappe.delete_doc("Holiday List", name, force=True, ignore_permissions=True)
	frappe.db.delete("Department Approver", {"name": ["like", f"{APPROVER_ROW}%"]})
	frappe.db.delete("Comment", {"name": ["like", f"{COMMENT_ROW}%"]})
	for name in frappe.get_all("Note", filters={"title": ["like", f"{NOTE_TITLE}%"]}, pluck="name"):
		frappe.delete_doc("Note", name, force=True, ignore_permissions=True)
	frappe.db.commit()


def run():
	frappe.set_user("Administrator")
	frappe.flags.mute_emails = True
	assert not frappe.flags.in_test, "bff_support_e2e must not run with in_test set -- see the module docstring"
	require_users()
	teardown()

	employee = frappe.db.get_value("Employee", {"user_id": REQUESTER, "status": "Active"}, "name")
	originals = {
		"employee": frappe.db.get_value("Employee", employee, ["department", "leave_approver"], as_dict=True) if employee else None,
		"committee_enabled": frappe.db.get_value("User", COMMITTEE, "enabled"),
	}
	real_backup_dir = bff_support._backup_dir
	try:
		_run(employee)
	finally:
		bff_support._backup_dir = real_backup_dir
		frappe.db.rollback()
		_restore(employee, originals)
		teardown()

	print(f"\nRESULT: {len(PASS)} passed, {len(FAIL)} failed")
	if FAIL:
		for label in FAIL:
			print(f"  FAILED: {label}")
		raise SystemExit(1)


def _restore(employee, originals):
	"""Put back the two existing records the run touched. The rollback already has; this is for a
	run that died after something committed."""
	frappe.set_user("Administrator")
	if employee and originals["employee"]:
		current = frappe.db.get_value("Employee", employee, ["department", "leave_approver"], as_dict=True)
		if current != originals["employee"]:
			frappe.db.set_value("Employee", employee, dict(originals["employee"]), update_modified=False)
		frappe.clear_document_cache("Employee", employee)
	if frappe.db.get_value("User", COMMITTEE, "enabled") != originals["committee_enabled"]:
		frappe.db.set_value("User", COMMITTEE, "enabled", originals["committee_enabled"], update_modified=False)
	frappe.clear_document_cache("User", COMMITTEE)
	frappe.db.commit()


def _run(employee):
	month_start = str(get_first_day(nowdate()))
	horizon = add_days(nowdate(), 60)

	print("\n--- 0  registration ---")
	for fn in (bff_support.backups, bff_support.my_holidays, bff_support.my_leave_approvers, bff_support.activity_feed, bff_support.switch_targets):
		check(
			f"{fn.__name__} is whitelisted for GET only, and not for Guests",
			fn in frappe.whitelisted
			and frappe.allowed_http_methods_for_whitelisted_func.get(fn) == ["GET"]
			and fn not in frappe.guest_methods,
			str(frappe.allowed_http_methods_for_whitelisted_func.get(fn)),
		)
	drifted = [user for user in USERS if "System Manager" in frappe.get_roles(user)]
	check("no role test user holds System Manager (else the refusals below prove nothing)", not drifted, str(drifted))

	print("\n--- 1  no role is shown a message dialog ---")
	for user in USERS:
		def read_all():
			bff_support.my_holidays(month_start, horizon)
			bff_support.my_leave_approvers()
			bff_support.activity_feed(20)
			return list(frappe.local.message_log)
		log = as_user(user, read_all)
		check(f"{user.split('.')[0]}: holidays, approvers and the feed raise no message dialogs", not log, str(log)[:120])

	_backups()
	_holidays(employee, month_start, horizon)
	_approvers(employee)
	_feed()
	_decision_reason_field()


# --- backups --------------------------------------------------------------------------------------


def _backups():
	print("\n--- 2  backups: System Manager only ---")
	for user in USERS:
		why = as_user(user, lambda: refused(bff_support.backups, frappe.PermissionError))
		check(f"{user.split('.')[0]}: backups refused", bool(why), why or "not refused")

	live, log = as_user("Administrator", with_log(bff_support.backups))
	files = [f for s in live["sets"] for f in s["files"]]
	limit = cint(frappe.get_system_settings("backup_limit")) or 3
	check(
		"Administrator gets the shaped listing, within the backup limit",
		set(live) == {"backup_limit", "encrypt_backup", "sets"}
		and live["backup_limit"] == limit
		and isinstance(live["encrypt_backup"], bool)
		and len(live["sets"]) <= limit
		and not log,
		f"{len(live['sets'])} sets, {len(files)} files",
	)
	configs = [f for f in files if f["kind"] == "site_config"]
	check(
		"live: no site_config backup gets a download link or reads as encrypted",
		all(f["download_url"] is None and f["encrypted"] is False for f in configs),
		f"{len(configs)} site_config files",
	)
	check(
		"live: every other file links to /backups/<its own name>",
		all(f["download_url"] == "/backups/" + f["file"] for f in files if f["kind"] != "site_config"),
	)

	manager = next(
		(
			user
			for user in frappe.get_all(
				"Has Role", filters={"role": "System Manager", "parenttype": "User"}, pluck="parent", distinct=True
			)
			if user != "Administrator" and frappe.db.get_value("User", user, ["enabled", "user_type"]) == (1, "System User")
		),
		None,
	)
	if manager:
		# Not named in the output: the only System Managers here are real people.
		answer, log = as_user(manager, with_log(bff_support.backups))
		check("a System Manager other than Administrator is answered, with no dialog", answer["sets"] == live["sets"] and not log)
	else:
		print("  note  no System Manager besides Administrator on this site -- role path not exercised")

	print("\n--- 3  backups: grouping, order, kinds and links, against fakes ---")
	directory = Path(tempfile.mkdtemp(prefix="bff-backups-"))
	try:
		expected = _fake_backups(directory)
		bff_support._backup_dir = lambda: directory
		got = as_user("Administrator", bff_support.backups)
	finally:
		shutil.rmtree(directory, ignore_errors=True)

	keys = [s["key"] for s in got["sets"]]
	check(
		"sets are newest-mtime first and cut at backup_limit (names sort differently, on purpose)",
		keys == expected["order"][:limit],
		str(keys),
	)
	by_key = {s["key"]: s for s in got["sets"]}
	full = by_key.get("20300105_010000") or {"files": []}
	check(
		"a full encrypted set: database, public, private, site_config -- in that order",
		[f["kind"] for f in full["files"]] == ["database", "public_files", "private_files", "site_config"],
		str([f["kind"] for f in full["files"]]),
	)
	check(
		"-enc marks the database and both .tgz archives encrypted, never the site_config",
		[f["encrypted"] for f in full["files"]] == [True, True, True, False],
		str([f["encrypted"] for f in full["files"]]),
	)
	check(
		"the site_config file has no download link; the others link to themselves",
		[f["download_url"] for f in full["files"]] == ["/backups/" + f["file"] for f in full["files"][:3]] + [None],
	)
	check(
		"sizes are bytes, timestamps are system-time strings, and a set's is its newest file's",
		all(f["size"] == expected["sizes"][f["file"]] and STAMP.match(f["modified"]) for f in full["files"])
		and full.get("modified") == max(f["modified"] for f in full["files"]),
		str(full.get("modified")),
	)
	plain = by_key.get("20300103_010000") or {"files": []}
	check(
		".tar archives are recognised, private before public in the match",
		[f["kind"] for f in plain["files"]] == ["database", "public_files", "private_files"]
		and not any(f["encrypted"] for f in plain["files"]),
		str([(f["kind"], f["encrypted"]) for f in plain["files"]]),
	)
	partial = by_key.get("20290101_000000") or {}
	check(
		"a -partial- database marks its set partial, and only that set",
		partial.get("partial") is True and not any(s["partial"] for s in got["sets"] if s["key"] != "20290101_000000"),
	)
	listed = {f["file"] for s in got["sets"] for f in s["files"]}
	check(
		"other sites' files, unknown suffixes, directories and stray files are skipped",
		not listed & expected["noise"] and not any(s["key"].startswith("20300108") for s in got["sets"]),
		str(sorted(listed & expected["noise"])),
	)


def _fake_backups(directory: Path) -> dict:
	"""Four sets whose names sort in a different order than their mtimes, plus noise."""
	slug = frappe.local.site.replace(".", "_")
	base = now_datetime().timestamp() - 86400
	layout = {
		# key: (mtime offset, [suffix, ...]) -- the full set gets one mtime per file
		"20300105_010000": (400, ["-database-enc.sql.gz", "-files-enc.tgz", "-private-files-enc.tgz", "-site_config_backup-enc.json"]),
		"20290101_000000": (300, ["-partial-database.sql.gz", "-site_config_backup.json"]),
		"20300103_010000": (200, ["-database.sql.gz", "-files.tar", "-private-files.tar"]),
		"20300104_010000": (100, ["-database.sql.gz"]),
	}
	sizes = {}
	for key, (offset, suffixes) in layout.items():
		for i, suffix in enumerate(suffixes):
			name = f"{key}-{slug}{suffix}"
			path = directory / name
			path.write_bytes(b"x" * (10 + len(sizes)))
			sizes[name] = path.stat().st_size
			stamp = base + offset - i * 5
			os.utime(path, (stamp, stamp))

	noise = {
		"readme.txt",
		"20300106_010000-other_site-database.sql.gz",
		f"20300108_010000-{slug}-unknown.bin",
	}
	for name in noise:
		(directory / name).write_bytes(b"noise")
		os.utime(directory / name, (base + 900, base + 900))
	(directory / f"20300107_010000-{slug}-database.sql.gz").mkdir()
	noise.add(f"20300107_010000-{slug}-database.sql.gz")
	return {"order": list(layout), "sizes": sizes, "noise": noise}


# --- holidays -------------------------------------------------------------------------------------


def _holidays(employee, month_start, horizon):
	print("\n--- 4  my_holidays: refusals ---")
	for label, args in (
		("a range that ends before it starts", ("2026-10-10", "2026-10-01")),
		("a range of more than 366 days", ("2026-01-01", "2027-01-03")),
		("a date that is not one", ("not-a-date", "2026-10-01")),
		("a blank date", ("", "2026-10-01")),
	):
		why = as_user(REQUESTER, lambda: refused(lambda: bff_support.my_holidays(*args), frappe.ValidationError))
		check(f"refused: {label}", bool(why), why or "not refused")
	whole_year = as_user(REQUESTER, lambda: refused(lambda: bff_support.my_holidays("2028-01-01", "2028-12-31")))
	check("a whole leap year (365 days apart) is allowed", whole_year is None, whole_year or "")

	print("\n--- 5  my_holidays: who has an employee ---")
	nobody = as_user(HEAD_OF_PROGRAMS, lambda: bff_support.my_holidays(month_start, horizon))
	check(
		"a user with no Employee gets employee None and no holidays",
		nobody == {"employee": None, "holiday_lists": [], "holidays": []},
		str(nobody),
	)
	if not employee:
		print("  note  the requester has no active Employee -- employee paths not exercised")
		return
	live = as_user(REQUESTER, lambda: bff_support.my_holidays(month_start, horizon))
	expected = _hrms_days(employee, getdate(month_start), getdate(horizon))
	check(
		"live: the requester's holidays are exactly the days HRMS calls holidays",
		live["employee"] == employee and [h["date"] for h in live["holidays"]] == expected
		and (live["holiday_lists"] or not expected),
		f"{len(expected)} expected, lists {live['holiday_lists']}",
	)

	print("\n--- 6  my_holidays: assignments starting inside the range ---")
	company = frappe.db.get_value("Employee", employee, "company")
	_holiday_list(HOLIDAYS_A, [
		("2030-01-20", "<p>Before any list applies</p>", 0, 0),
		("2030-03-10", "<p>Alpha <b>Day</b></p>", 0, 0),
		("2030-05-15", "Sunday", 1, 0),
		("2030-06-10", "Alpha in June -- B applies then", 0, 0),
	])
	_holiday_list(HOLIDAYS_B, [
		("2030-02-14", "Bravo February", 0, 0),
		("2030-04-20", "Bravo April -- A applies then", 0, 0),
		("2030-06-20", "Bravo half day", 0, 1),
	])
	# The company's list from February; the employee's own from March, twice (so two segments
	# with the same list must join), then B again from June.
	_assign("Company", company, HOLIDAYS_B, "2030-02-01")
	_assign("Employee", employee, HOLIDAYS_A, "2030-03-01")
	_assign("Employee", employee, HOLIDAYS_A, "2030-05-01")
	_assign("Employee", employee, HOLIDAYS_B, "2030-06-01")

	got = as_user(REQUESTER, lambda: bff_support.my_holidays("2030-01-15", "2030-07-31"))
	dates = [h["date"] for h in got["holidays"]]
	check(
		"the days match HRMS asked day by day",
		dates == _hrms_days(employee, getdate("2030-01-15"), getdate("2030-07-31")),
		str(dates),
	)
	check(
		"company list in February, the employee's own from March, B again from June",
		# Only this suite's own lists: a site may already assign the company a real calendar, which
		# still applies in 2030 but has no days in it.
		dates == ["2030-02-14", "2030-03-10", "2030-05-15", "2030-06-20"]
		and [name for name in got["holiday_lists"] if name in (HOLIDAYS_A, HOLIDAYS_B)] == [HOLIDAYS_B, HOLIDAYS_A],
		str(got["holiday_lists"]),
	)
	by_date = {h["date"]: h for h in got["holidays"]}
	check(
		"descriptions are plain text; weekly_off and is_half_day are booleans",
		by_date.get("2030-03-10", {}).get("description") == "Alpha Day"
		and by_date.get("2030-05-15", {}).get("weekly_off") is True
		and by_date.get("2030-06-20", {}).get("is_half_day") is True
		and by_date.get("2030-02-14", {}).get("is_half_day") is False,
		str(by_date.get("2030-03-10")),
	)
	tail = as_user(REQUESTER, lambda: bff_support.my_holidays("2030-06-05", "2030-06-30"))
	check(
		"a range starting after the last assignment still gets the list in force on its first day",
		[h["date"] for h in tail["holidays"]] == ["2030-06-20"] and tail["holiday_lists"] == [HOLIDAYS_B],
		str(tail),
	)


def _hrms_days(employee, start, end) -> list[str]:
	"""Every day in the range that the list HRMS applies ON THAT DAY has as a holiday."""
	days, holidays_of = [], {}
	day = start
	while day <= end:
		holiday_list = get_holiday_list_for_employee(employee, raise_exception=False, as_on=day)
		if holiday_list:
			if holiday_list not in holidays_of:
				holidays_of[holiday_list] = {
					getdate(d)
					for d in frappe.get_all(
						"Holiday", filters={"parenttype": "Holiday List", "parent": holiday_list}, pluck="holiday_date"
					)
				}
			if day in holidays_of[holiday_list]:
				days.append(str(day))
		day += timedelta(days=1)
	return days


def _holiday_list(name, rows):
	frappe.get_doc(
		{
			"doctype": "Holiday List",
			"holiday_list_name": name,
			"from_date": "2030-01-01",
			"to_date": "2030-12-31",
			"holidays": [
				{"holiday_date": d, "description": text, "weekly_off": weekly, "is_half_day": half}
				for d, text, weekly, half in rows
			],
		}
	).insert(ignore_permissions=True)


def _assign(applicable_for, assigned_to, holiday_list, from_date):
	doc = frappe.get_doc(
		{
			"doctype": "Holiday List Assignment",
			"applicable_for": applicable_for,
			"assigned_to": assigned_to,
			"holiday_list": holiday_list,
			"from_date": from_date,
		}
	)
	doc.insert(ignore_permissions=True)
	doc.submit()


# --- leave approvers ------------------------------------------------------------------------------


def _approvers(employee):
	print("\n--- 7  my_leave_approvers ---")
	mandatory = bool(cint(frappe.db.get_single_value("HR Settings", "leave_approver_mandatory_in_leave_application")))
	nobody = as_user(HEAD_OF_PROGRAMS, bff_support.my_leave_approvers)
	check(
		"a user with no Employee: no approvers, the site's mandatory flag",
		nobody == {"employee": None, "is_mandatory": mandatory, "default": None, "approvers": []},
		str(nobody),
	)
	if not employee:
		print("  note  the requester has no active Employee -- approver paths not exercised")
		return
	live = as_user(REQUESTER, bff_support.my_leave_approvers)
	users = [a.get("user") for a in live["approvers"]]
	check(
		"live: shaped, mandatory flag from HR Settings, default one of the approvers",
		set(live) == {"employee", "is_mandatory", "default", "approvers"}
		and live["employee"] == employee
		and live["is_mandatory"] is mandatory
		and all(set(a) == {"user", "full_name"} and a["full_name"] for a in live["approvers"])
		and (live["default"] in users if users else live["default"] is None),
		f"{len(users)} approvers, mandatory {live['is_mandatory']}",
	)

	leaf = frappe.get_all(
		"Department",
		filters={"is_group": 0, "parent_department": ["is", "set"]},
		fields=["name", "parent_department"],
		order_by="lft asc",
		limit=1,
	)
	if not leaf:
		print("  note  no department with a parent on this site -- the tree walk is not exercised")
		return
	department, parent = leaf[0].name, leaf[0].parent_department
	# Own department: the requester themself first (dropped), then two colleagues. The parent:
	# a third, and a duplicate of one already offered (kept once, at its nearest place).
	rows = [
		(department, REQUESTER, 1), (department, HEAD_OF_FINANCE, 2), (department, FINANCE_OFFICER, 3),
		(parent, EXECUTIVE_DIRECTOR, 1), (parent, HEAD_OF_FINANCE, 2),
	]
	for i, (dept, approver, idx) in enumerate(rows):
		frappe.get_doc(
			{
				"doctype": "Department Approver",
				"name": f"{APPROVER_ROW}{i}",
				"parent": dept,
				"parenttype": "Department",
				"parentfield": "leave_approvers",
				"approver": approver,
				"idx": idx,
			}
		).db_insert()

	frappe.db.set_value("Employee", employee, {"department": department, "leave_approver": COMMITTEE}, update_modified=False)
	frappe.db.set_value("User", COMMITTEE, "enabled", 0, update_modified=False)
	got = as_user(REQUESTER, bff_support.my_leave_approvers)
	check(
		"own department first, then up the tree; self, duplicates and a disabled leave_approver dropped",
		[a["user"] for a in got["approvers"]] == [HEAD_OF_FINANCE, FINANCE_OFFICER, EXECUTIVE_DIRECTOR],
		str([a["user"] for a in got["approvers"]]),
	)
	check(
		"a dropped leave_approver gives way to the own department's first approver",
		got["default"] == HEAD_OF_FINANCE,
		str(got["default"]),
	)
	full_name = frappe.db.get_value("User", HEAD_OF_FINANCE, "full_name") or HEAD_OF_FINANCE
	check("full_name is the user's", bool(got["approvers"]) and got["approvers"][0]["full_name"] == full_name)

	frappe.db.set_value("Employee", employee, "leave_approver", OPERATIONS, update_modified=False)
	got = as_user(REQUESTER, bff_support.my_leave_approvers)
	check(
		"an enabled Employee.leave_approver comes first and is the default",
		[a["user"] for a in got["approvers"]] == [OPERATIONS, HEAD_OF_FINANCE, FINANCE_OFFICER, EXECUTIVE_DIRECTOR]
		and got["default"] == OPERATIONS,
		f"{[a['user'] for a in got['approvers']]} / {got['default']}",
	)

	frappe.db.set_value("Employee", employee, "leave_approver", None, update_modified=False)
	frappe.db.delete("Department Approver", {"name": ["in", [f"{APPROVER_ROW}1", f"{APPROVER_ROW}2"]]})
	got = as_user(REQUESTER, bff_support.my_leave_approvers)
	check(
		"with nobody left in the own department, the first approver up the tree is the default",
		[a["user"] for a in got["approvers"]] == [EXECUTIVE_DIRECTOR, HEAD_OF_FINANCE] and got["default"] == EXECUTIVE_DIRECTOR,
		f"{[a['user'] for a in got['approvers']]} / {got['default']}",
	)


# --- activity feed --------------------------------------------------------------------------------


def _feed():
	print("\n--- 8  activity_feed: fixtures ---")
	base = now_datetime() + timedelta(seconds=1)
	requisition = _readable_by(HEAD_OF_PROGRAMS, "Activity Requisition")
	order = _readable_by(PURCHASER, "Purchase Order")
	rfq = _readable_by(PURCHASER, "Request for Quotation")
	# A Return for Correction as it is really written: FoLT's comment with the reason, then
	# frappe's bare state a few milliseconds later.
	if requisition:
		_comment("Activity Requisition", requisition, f"Draft — {REASON}", base)
		_comment("Activity Requisition", requisition, "Draft", base + timedelta(milliseconds=3))
	_comment("Purchase Order", GONE, "Approved", base + timedelta(seconds=2))
	if order:
		_comment("Purchase Order", order, "E2E BFF a remark, not a state", base + timedelta(seconds=3))
	if rfq:
		_comment("Request for Quotation", rfq, "Draft", base + timedelta(seconds=4))

	_note("E2E BFF Office closed Friday", public=1, content="<p>Closed <script>alert(1)</script>Friday</p>")
	_note("E2E BFF Payroll cut-off", public=1, expires=now_datetime() + timedelta(days=2))
	_note("E2E BFF Expired notice", public=1, expires=now_datetime() - timedelta(days=1))
	_note("E2E BFF Private jotting", public=0)

	print("\n--- 9  activity_feed: limits and shape ---")
	for limit, most in ((500, 50), (0, 20), (-3, 1), ("7", 7)):
		got = as_user("Administrator", lambda: bff_support.activity_feed(limit))
		check(f"limit {limit!r} returns at most {most}", len(got["items"]) <= most, str(len(got["items"])))
	every = as_user("Administrator", lambda: bff_support.activity_feed(50))["items"]
	check(
		"every item is shaped, with a known kind and tone",
		all(
			set(i) == ITEM_KEYS
			and i["kind"] in KINDS
			and (i["tone"] in TONES or i["tone"] is None)
			and isinstance(i["derived"], bool)
			and (i["state"] is not None or i["kind"] in ("note", "derived"))
			for i in every
		),
		f"{len(every)} items",
	)

	print("\n--- 10  activity_feed: only what the user can read ---")
	for user, absent, present in (
		(HEAD_OF_PROGRAMS, "Purchase Order", "Activity Requisition"),
		(PURCHASER, "Activity Requisition", "Purchase Order"),
	):
		feed, log = as_user(user, with_log(lambda: bff_support.activity_feed(50)))
		readable = as_user(user, lambda: {dt for dt in step_forms.SPA_DOCTYPES if frappe.has_permission(dt, "read")})
		items = feed["items"]
		who = user.split(".")[0]
		check(f"{who}: no dialog, and every item is a doctype they read", not log and all(i["doctype"] in readable for i in items), str(log)[:120])
		check(
			f"{who}: every item is a document they may open",
			all(frappe.has_permission(i["doctype"], doc=i["name"], user=user) for i in items),
		)
		check(f"{who}: nothing from {absent}", not any(i["doctype"] == absent for i in items))
		has_any = frappe.db.count("Comment", {"comment_type": "Workflow", "reference_doctype": present}) > 0
		check(f"{who}: {present} moves are there", any(i["doctype"] == present for i in items) or not has_any)
		titles = as_user(user, lambda: [(i["title"], spa._title_of(frappe.get_doc(i["doctype"], i["name"]))) for i in items])
		check(f"{who}: titles are the ones the Tasks screen shows", all(a == b for a, b in titles), str([t for t in titles if t[0] != t[1]][:2]))
		check(f"{who}: a comment on a deleted document is skipped", not any(i["name"] == GONE for i in items))

	if requisition:
		items = as_user(HEAD_OF_PROGRAMS, lambda: bff_support.activity_feed(20))["items"]
		ours = [i for i in items if i["name"] == requisition and i["at"] >= str(base)]
		check(
			"a turn-down's two comments are one row: turned down, with its reason and the earlier time",
			len(ours) == 1 and ours[0]["kind"] == "turned_down" and ours[0]["reason"] == REASON
			and ours[0]["state"] == "Draft" and ours[0]["at"] == str(base),
			str([(i["state"], i["kind"], i["reason"]) for i in ours]),
		)
	else:
		print("  note  no Activity Requisition the Head of Programs can read -- turn-down pair not exercised")

	items = as_user(PURCHASER, lambda: bff_support.activity_feed(20))["items"]
	if order:
		remark = [i for i in items if i["name"] == order and i["at"] >= str(base)]
		check(
			"a comment whose head is not a state is a note, with no state or tone",
			len(remark) == 1 and remark[0]["kind"] == "note" and remark[0]["state"] is None and remark[0]["tone"] is None,
			str(remark[:1]),
		)
	if rfq:
		plain = [i for i in items if i["doctype"] == "Request for Quotation" and i["name"] == rfq and i["at"] >= str(base)]
		check(
			"a doctype with no workflow (RFQ) reads every comment as a note",
			len(plain) == 1 and plain[0]["kind"] == "note" and plain[0]["state"] is None,
			str(plain[:1]),
		)

	outsider = frappe.db.get_value("User", {"user_type": "Website User", "enabled": 1}, "name")
	if outsider:
		got, log = as_user(outsider, with_log(lambda: bff_support.activity_feed(20)))
		# Not named: the only Website Users here are real supplier contacts.
		check("a user who reads none of the SPA doctypes gets no items, and no dialog", got["items"] == [] and not log)

	print("\n--- 11  activity_feed: announcements ---")
	notes = as_user(REQUESTER, lambda: bff_support.activity_feed(5))["announcements"]
	titles = [n["title"] for n in notes]
	check(
		"public unexpired notes are announced; expired and private ones are not",
		"E2E BFF Office closed Friday" in titles and "E2E BFF Payroll cut-off" in titles
		and "E2E BFF Expired notice" not in titles and "E2E BFF Private jotting" not in titles,
		str(titles),
	)
	by_title = {n["title"]: n for n in notes}
	closed = by_title.get("E2E BFF Office closed Friday") or {}
	cutoff = by_title.get("E2E BFF Payroll cut-off") or {}
	check(
		"an announcement is shaped; no expiry reads as null, an expiry as a timestamp",
		set(closed) == {"name", "title", "content", "by", "by_name", "at", "expires"}
		and closed.get("expires") is None
		and bool(cutoff.get("expires")),
		str(sorted(closed)),
	)
	check("announcement HTML arrives sanitised", "<script" not in (closed.get("content") or ""), closed.get("content") or "")


def _readable_by(user, doctype) -> str | None:
	"""The newest `doctype` document `user` may read, if any."""
	return as_user(
		user,
		lambda: next(
			(name for name in frappe.get_all(doctype, order_by="modified desc", limit=20, pluck="name")
			 if frappe.has_permission(doctype, doc=name)),
			None,
		),
	)


def _comment(doctype, name, content, creation):
	# db_insert: a Workflow comment on a deleted document has to be possible to write -- the
	# Dynamic Link check would refuse it -- and the timestamps have to be exactly these.
	frappe.get_doc(
		{
			"doctype": "Comment",
			"name": f"{COMMENT_ROW}{frappe.generate_hash(length=8)}",
			"comment_type": "Workflow",
			"reference_doctype": doctype,
			"reference_name": name,
			"content": content,
			"owner": "Administrator",
			"comment_email": "Administrator",
			"creation": creation,
			"modified": creation,
		}
	).db_insert()


def _note(title, public, content="<p>E2E</p>", expires=None):
	frappe.get_doc(
		{"doctype": "Note", "title": title, "public": public, "content": content, "expire_notification_on": expires}
	).insert(ignore_permissions=True)


# --- the decision reason field --------------------------------------------------------------------


def _decision_reason_field():
	print("\n--- 12  switch_targets: only for those who may impersonate ---")
	for user in USERS:
		refusal = as_user(user, lambda: refused(bff_support.switch_targets, frappe.PermissionError))
		check(f"{user} cannot list switch targets (no User impersonate)", refusal is not None)
	targets = as_user("Administrator", bff_support.switch_targets)["users"]
	logins = {t["user"] for t in targets}
	check("Administrator gets enabled desk users, never itself or Guest", targets and not logins & {"Administrator", "Guest"}, str(len(targets)))
	check("every target has a name and the four fields the contract names", all(set(t) == {"user", "full_name", "label", "image"} and t["full_name"] for t in targets))
	labels = {t["user"]: t["label"] for t in targets}
	check("a role holder reads as their role", labels.get(HEAD_OF_FINANCE) not in (None, "Employee", "All", "Desk User"), str(labels.get(HEAD_OF_FINANCE)))
	designation = frappe.db.get_value("Employee", {"user_id": REQUESTER, "status": "Active"}, "designation")
	check(
		"someone with only the Employee role reads as their designation, else Employee -- never blank",
		labels.get(REQUESTER) == (designation or "Employee"),
		f"{labels.get(REQUESTER)} (designation {designation})",
	)
	narrowed = as_user("Administrator", lambda: bff_support.switch_targets("fin"))["users"]
	check("q narrows by name or login", narrowed and all("fin" in (t["user"] + t["full_name"]).lower() for t in narrowed), str([t["user"] for t in narrowed]))

	print("\n--- 13  Leave Application.folt_decision_reason ---")
	field = frappe.get_meta("Leave Application").get_field("folt_decision_reason")
	check(
		"installed: Small Text, permlevel 1, after the approver's name, no_copy, print_hide",
		bool(field)
		and field.fieldtype == "Small Text"
		and field.permlevel == 1
		and field.insert_after == "leave_approver_name"
		and field.no_copy == 1
		and field.print_hide == 1
		and not field.allow_on_submit,
		str(field and (field.fieldtype, field.permlevel, field.insert_after)),
	)
	with open(frappe.get_app_path("folt_customizations", "fixtures", "custom_field.json")) as f:
		row = next((r for r in json.load(f) if r["name"] == "Leave Application-folt_decision_reason"), {})
	installed = frappe.db.get_value("Custom Field", "Leave Application-folt_decision_reason", "modified")
	# migrate skips a fixture whose `modified` is not newer than the row's, so an edit to the JSON
	# without a bump would leave this behind silently.
	check(
		"the installed row is the fixture's version",
		bool(row) and str(installed)[:19] == row["modified"][:19],
		f"{str(installed)[:19]} vs {row.get('modified', '')[:19]}",
	)
	meta = frappe.get_meta("Leave Application")
	check(
		"the applicant (Employee) reads it but cannot write it",
		1 in meta.get_permlevel_access("read", user=REQUESTER) and 1 not in meta.get_permlevel_access("write", user=REQUESTER),
	)
	writers = {p.role for p in meta.permissions if p.permlevel == 1 and p.write}
	check(
		"Leave Approver, HR User and HR Manager write it",
		{"Leave Approver", "HR User", "HR Manager"} <= writers,
		str(sorted(writers)),
	)
