"""Five reads the FoLT BFF needs that Frappe cannot answer for the person asking.

The Go BFF (folt-workflow-ui, openapi/bff.v1.yaml) talks to Frappe AS THE USER, through
/api/resource and /api/method, so every answer it gets is already permission-scoped by Frappe. For
almost everything that is the point. For these four it is the problem: each is a question the user
is entitled to have answered, about their own things, that Frappe will only answer by reading
something the user may not read.

  backups()             The Desk's Backups page builds its list in `get_context`, which is not
                        whitelisted, and lists only the database dumps. System Manager only.
  my_holidays()         HRMS's own `get_holidays_for_employee` asks for Holiday List read, which
                        the Employee role does not have -- so an employee cannot see their own
                        public holidays. The filter "this employee's own list" is the check here.
  my_leave_approvers()  HRMS's approval details ask for Department read (the Employee role has
                        select only), and its default-approver lookup does not walk up the
                        department tree although its approver dropdown does.
  switch_targets()      The person menu's "Switch user" list: the people an impersonator may switch
                        to, each with a line saying who they are (designation, else main role).
                        That line needs Employee and Has Role reads; the list is refused outright
                        unless frappe.has_permission("User", "impersonate") -- the same check
                        frappe's own impersonate() makes before it switches anyone.
  activity_feed()       The Home feed is the workflow timeline, which lives in Comment rows, and
                        the Employee role cannot read Comment. Each row is permission-checked
                        against the DOCUMENT it is about, so a user sees exactly the moves on the
                        documents they could open.

THE CONTRACT IS EXTERNAL. docs/bff-upstream-map.md section 4 in folt-workflow-ui fixes each
function's arguments, checks and return shape, and the BFF decodes these dicts field for field. A
change here is a change to that contract: make it there first.

ASK, NEVER CATCH. Every permission question goes through `frappe.has_permission` /
`frappe.db.exists` before anything that could `frappe.throw`. A caught throw has already queued
its message on `frappe.local.message_log`, which ships to the client as `_server_messages` with a
successful response -- and the BFF shows those as notices (the folt_tasks.my_tasks trap). The
bff_support_e2e suite checks every role's calls leave the log empty.

Nothing here writes. Nothing needs a hooks.py entry: the BFF calls
GET /api/method/folt_customizations.bff_support.<fn>.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import frappe
from frappe import _
from frappe.utils import cint, convert_utc_to_system_timezone, get_fullname, getdate, now, strip_html
from hrms.utils.holiday_list import get_holiday_list_for_employee

from folt_customizations import spa, step_forms, workflow_shape
from folt_customizations.document_guide import _parse_comment

# --- backups --------------------------------------------------------------------------------------

# Matched against what follows the site slug, with any `-enc` removed. Private files come before
# public ones because `-private-files.tar` also ends in `-files.tar`. `.tgz` is what Frappe writes
# for a compressed files backup (utils/backups.py set_backup_file_name: `ext = "tgz" if
# self.compress_files else "tar"`).
_BACKUP_KINDS = (
	("-database.sql.gz", "database"),
	("-private-files.tar", "private_files"),
	("-private-files.tgz", "private_files"),
	("-files.tar", "public_files"),
	("-files.tgz", "public_files"),
	("-site_config_backup.json", "site_config"),
)
_KIND_ORDER = {"database": 0, "public_files": 1, "private_files": 2, "site_config": 3}


@frappe.whitelist(methods=["GET"])
def backups() -> dict:
	"""The backup sets on disk, newest first, with a download link for every file that may have one.

	THE SITE CONFIG IS LISTED BUT NEVER LINKED. `*-site_config_backup.json` is a copy of
	site_config.json: the database credentials and the `encryption_key` that decrypts every
	Password field on the site, in plain text. Showing that the file exists tells an administrator
	the set is complete; a link would let a stolen System Manager session walk off with the keys,
	so it stays retrievable on the server only. Its `encrypted` is always False for the same
	reason it is dangerous: Frappe puts `-enc` in its name but `backup_encryption` gpg-encrypts
	only the database and the two file archives.
	"""
	frappe.only_for("System Manager", message=True)

	limit = cint(frappe.get_system_settings("backup_limit")) or 3
	out = {
		"backup_limit": limit,
		"encrypt_backup": bool(cint(frappe.get_system_settings("encrypt_backup"))),
		"sets": [],
	}
	directory = _backup_dir()
	if not directory.is_dir():
		return out

	# The grouping rule is frappe's own (desk/page/backups/backups.py delete_downloadable_backups),
	# so a set here is exactly what the hourly pruning treats as one.
	slug = "-" + frappe.local.site.replace(".", "_")
	sets: dict[str, dict] = {}
	for path in directory.iterdir():
		# A name without this site's slug is not one of this site's backups -- whatever put it
		# there, it gets no download link from here.
		if not path.is_file() or slug not in path.name:
			continue
		key, tail = path.name.rsplit(slug, 1)
		# `-enc` is looked for after the slug only, so a site whose own name contains "-enc"
		# cannot make every file read as encrypted.
		kind = next((kind for suffix, kind in _BACKUP_KINDS if tail.replace("-enc", "").endswith(suffix)), None)
		if not kind:
			continue

		stat = path.stat()
		entry = sets.setdefault(key, {"key": key, "partial": False, "mtime": 0.0, "files": []})
		entry["partial"] = entry["partial"] or "-partial-" in tail
		entry["mtime"] = max(entry["mtime"], stat.st_mtime)
		linked = kind != "site_config"
		entry["files"].append(
			{
				"file": path.name,
				"kind": kind,
				"size": int(stat.st_size),
				"modified": _system_time(stat.st_mtime),
				"encrypted": linked and "-enc" in tail,
				"download_url": "/backups/" + path.name if linked else None,
			}
		)

	# By the newest file's real mtime. The Desk page sorts by a formatted "%a %b %d ..." string,
	# which orders by weekday name.
	for entry in sorted(sets.values(), key=lambda s: s["mtime"], reverse=True)[:limit]:
		entry["files"].sort(key=lambda f: (_KIND_ORDER[f["kind"]], f["file"]))
		entry["modified"] = _system_time(entry.pop("mtime"))
		out["sets"].append(entry)
	return out


def _backup_dir() -> Path:
	"""The same directory the Desk's Backups page reads. A function so the e2e can point it at fakes."""
	return Path(frappe.utils.get_site_path("private", "backups"))


def _system_time(timestamp: float) -> str:
	return convert_utc_to_system_timezone(datetime.fromtimestamp(timestamp, tz=timezone.utc)).strftime("%Y-%m-%d %H:%M:%S")


# --- holidays -------------------------------------------------------------------------------------


@frappe.whitelist(methods=["GET"])
def my_holidays(from_date: str, to_date: str) -> dict:
	"""The session employee's holidays between two dates, from the list HRMS would apply on each day.

	WHICH LIST IS HRMS'S ANSWER, NOT OURS. `get_holiday_list_for_employee(as_on=d)` takes the
	latest submitted employee-level Holiday List Assignment starting on or before `d`, and falls
	back to the company's only when the employee has none; Employee.holiday_list and
	Company.default_holiday_list are ignored in HRMS v16. Choosing between those rows here would
	sooner or later disagree with the leave cost and the attendance HRMS computes from the same
	days. So the range is cut at every date an assignment for the employee or their company
	starts, HRMS is asked once per piece, and the pieces that got the same list are joined.

	`get_all`, not a permission check, reads the holidays: the Employee role has no Holiday List
	read (why HRMS's own endpoint throws for it), and "the list this employee is assigned" is the
	check.
	"""
	start, end = _date(from_date), _date(to_date)
	if end < start:
		frappe.throw(_("The range ends before it starts."), title=_("Invalid range"))
	if (end - start).days > 366:
		frappe.throw(
			_("A range can span at most 366 days; {0} to {1} is {2} days.").format(
				frappe.bold(str(start)), frappe.bold(str(end)), (end - start).days
			),
			title=_("Invalid range"),
		)

	employee = frappe.db.get_value(
		"Employee", {"user_id": frappe.session.user, "status": "Active"}, ["name", "company"], as_dict=True
	)
	if not employee:
		return {"employee": None, "holiday_lists": [], "holidays": []}

	# Every day from one boundary to the day before the next gets the same answer from HRMS: no
	# assignment that could change it starts in between.
	starts = frappe.get_all(
		"Holiday List Assignment",
		filters={
			"docstatus": 1,
			"assigned_to": ["in", [employee.name, employee.company]],
			"from_date": ["between", [start, end]],
		},
		pluck="from_date",
		distinct=True,
		order_by="from_date asc",
	)
	boundaries = sorted({start, *(getdate(d) for d in starts)})

	segments: list[list] = []  # [holiday_list, first day, last day]
	for i, boundary in enumerate(boundaries):
		last = boundaries[i + 1] - timedelta(days=1) if i + 1 < len(boundaries) else end
		holiday_list = get_holiday_list_for_employee(employee.name, raise_exception=False, as_on=boundary)
		if segments and segments[-1][0] == holiday_list:
			segments[-1][2] = last
		else:
			segments.append([holiday_list, boundary, last])

	holiday_lists, holidays = [], []
	for holiday_list, first, last in segments:
		if not holiday_list:
			continue
		if holiday_list not in holiday_lists:
			holiday_lists.append(holiday_list)
		for row in frappe.get_all(
			"Holiday",
			filters={"parenttype": "Holiday List", "parent": holiday_list, "holiday_date": ["between", [first, last]]},
			fields=["holiday_date", "description", "weekly_off", "is_half_day"],
			order_by="holiday_date asc",
		):
			holidays.append(
				{
					"date": str(row.holiday_date),
					# A Text Editor field: the BFF and the SPA treat it as plain text.
					"description": strip_html(row.description or "").strip(),
					"weekly_off": bool(row.weekly_off),
					"is_half_day": bool(row.is_half_day),
				}
			)
	return {"employee": employee.name, "holiday_lists": holiday_lists, "holidays": holidays}


def _date(value: str):
	"""A date argument, refused by name when it is not one. getdate answers a blank with today and
	a zero date with None, neither of which is what was asked for."""
	parsed = getdate(value) if value else None
	if not parsed:
		frappe.throw(_("{0} is not a date.").format(frappe.bold(value or _("(blank)"))), title=_("Invalid range"))
	return parsed


# --- leave approvers ------------------------------------------------------------------------------


@frappe.whitelist(methods=["GET"])
def my_leave_approvers() -> dict:
	"""Who the session employee may send a leave application to, which one to preselect, and
	whether choosing one is mandatory.

	MANDATORY IS ENFORCED BY THE BFF. HR Settings' `leave_approver_mandatory_in_leave_application`
	is honoured by HRMS only in the browser (leave_application.js toggles `reqd`); nothing on the
	server checks it, so the BFF has to, and it reads the flag here.

	THE CANDIDATES WALK THE DEPARTMENT TREE. The employee's own `leave_approver` first, then the
	Department Approver rows of their department and of every department above it, nearest first --
	what HRMS's approver dropdown (department_approver.get_approvers) offers, rather than its
	default lookup (get_employee_leave_approver), which stops at the employee's own department.
	`get_all`, because the Employee role has select only on Department.
	"""
	is_mandatory = bool(
		cint(frappe.db.get_single_value("HR Settings", "leave_approver_mandatory_in_leave_application"))
	)
	user = frappe.session.user
	employee = frappe.db.get_value(
		"Employee", {"user_id": user, "status": "Active"}, ["name", "department", "leave_approver"], as_dict=True
	)
	if not employee:
		return {"employee": None, "is_mandatory": is_mandatory, "default": None, "approvers": []}

	candidates = [employee.leave_approver] if employee.leave_approver else []
	own_department: list[str] = []
	bounds = employee.department and frappe.db.get_value("Department", employee.department, ["lft", "rgt"])
	if bounds:
		lft, rgt = bounds
		ancestors = frappe.get_all(
			"Department",
			filters={"lft": ["<=", lft], "rgt": [">=", rgt]},
			fields=["name", "lft"],
			order_by="lft desc",
		)
		depth = {row.name: i for i, row in enumerate(ancestors)}
		rows = frappe.get_all(
			"Department Approver",
			filters={"parenttype": "Department", "parentfield": "leave_approvers", "parent": ["in", list(depth)]},
			fields=["parent", "approver", "idx"],
		)
		rows.sort(key=lambda row: (depth[row.parent], row.idx))
		candidates += [row.approver for row in rows]
		own_department = [row.approver for row in rows if row.parent == employee.department]

	# Nobody approves their own leave, and a disabled account cannot sign in to approve anything.
	unique = [u for u in dict.fromkeys(candidates) if u and u != user]
	users = {}
	if unique:
		for row in frappe.get_all("User", filters={"name": ["in", unique]}, fields=["name", "full_name", "enabled"]):
			users[row.name] = row
	kept = [u for u in unique if u in users and users[u].enabled]

	# Spelled out as the map gives it. Since the candidates are already in this order it is the
	# first kept approver -- written this way so the rule reads, not just the result.
	if employee.leave_approver in kept:
		default = employee.leave_approver
	else:
		default = next((u for u in own_department if u in kept), None) or (kept[0] if kept else None)

	return {
		"employee": employee.name,
		"is_mandatory": is_mandatory,
		"default": default,
		"approvers": [{"user": u, "full_name": users[u].full_name or u} for u in kept],
	}


# --- activity feed --------------------------------------------------------------------------------


@frappe.whitelist(methods=["GET"])
def activity_feed(limit: int = 20) -> dict:
	"""The latest workflow moves on documents this user can open, and the site's announcements.

	The moves are the Workflow comments `apply_workflow` writes on every transition -- the same
	source as each document's timeline (document_guide._timeline), read here across documents.
	Comment is not readable by the Employee role, so this reads it with `get_all` and asks, per
	document, whether the user may read THAT document. Rows on documents they cannot read are
	skipped, so the feed is short a row rather than the BFF being handed one to hide.

	The BFF then applies its own policy on top -- payroll and own-claims guards, links -- see the
	upstream map.
	"""
	limit = max(1, min(cint(limit) or 20, 50))
	readable = [dt for dt in step_forms.SPA_DOCTYPES if frappe.has_permission(dt, "read")]
	return {"items": _feed_items(readable, limit) if readable else [], "announcements": _announcements()}


def _feed_items(readable: list[str], limit: int) -> list[dict]:
	page = limit * 4
	documents: dict[tuple, str | None] = {}  # (doctype, name) -> title, or None when unreadable
	shapes, tones, titles = {}, {}, spa._Titles()
	items: list[dict] = []

	# Three pages at most: a user who can read little of a busy site gets a short feed, not a
	# query per comment the site has ever written.
	for k in range(3):
		rows = frappe.get_all(
			"Comment",
			filters={"comment_type": "Workflow", "reference_doctype": ["in", readable]},
			fields=["reference_doctype", "reference_name", "content", "owner", "creation"],
			order_by="creation desc",
			limit_start=k * page,
			limit=page,
		)
		for row in rows:
			key = (row.reference_doctype, row.reference_name)
			if key not in documents:
				documents[key] = _readable_title(*key, titles)
			if documents[key] is None:
				continue

			doctype = row.reference_doctype
			if doctype not in shapes:
				# None for Request for Quotation and Supplier Quotation, which have no workflow:
				# every comment on them reads as a note.
				shapes[doctype] = workflow_shape.shape(doctype) or {}
				tones[doctype] = spa.state_tones(doctype)
			item = _feed_item(row, documents[key], shapes[doctype], tones[doctype])

			if _same_move(items[-1] if items else None, item):
				# A turn-down files two comments milliseconds apart -- FoLT's with the reason from
				# on_update, then frappe's bare state from apply_workflow -- and they are one event.
				# document_guide._collapse merges them for the timeline; doing the same here keeps
				# the bare half from reading as a move forward (a Return for Correction lands on
				# Draft, which is not a turn-down state by itself). The merged row keeps the reason,
				# the kind that goes with it and the earlier time.
				kept = items[-1]
				if item["reason"]:
					kept.update(reason=item["reason"], kind=item["kind"])
				kept["at"] = min(kept["at"], item["at"])
				continue

			# Checked before appending rather than after, so the row that completes the page can
			# still absorb its other half from the next comment.
			if len(items) == limit:
				return items
			items.append(item)
		if len(rows) < page:
			break
	return items


def _readable_title(doctype: str, name: str, titles) -> str | None:
	"""The document's title if this user may read it, else None.

	`db.exists` FIRST. In v16 `has_permission` loads a string `doc` with `get_lazy_doc`, which
	for a deleted document raises DoesNotExistError through frappe.throw -- having already queued
	"<Doctype> <name> not found" on message_log, where catching it no longer helps.
	"""
	if not name or not frappe.db.exists(doctype, name) or not frappe.has_permission(doctype, doc=name):
		return None

	# spa._title_of's rules, without loading the document: the same title the Tasks screen shows.
	meta = frappe.get_meta(doctype)
	field = spa._TITLE_OVERRIDE.get(doctype) or meta.title_field or spa._TITLE_FALLBACK.get(doctype)
	value = field and frappe.db.get_value(doctype, name, field)
	if not value:
		return name
	docfield = meta.get_field(field)
	if docfield and docfield.fieldtype == "Link":
		return titles.get(docfield.options, value) or value
	return str(value)


def _feed_item(row, title: str, shaped: dict, tones: dict) -> dict:
	"""One feed row. document_guide._kind's rules, less the previous-state pair a lone row lacks."""
	state, reason, derived = _parse_comment((row.content or "").strip())
	if state not in (shaped.get("roles_by_state") or {}):
		state = None

	if derived:
		kind = "derived"
	elif state is None:
		kind = "note"
	# Only turn-downs carry a reason: spa.act parks one for nothing else.
	elif ((shaped.get("off_path") or {}).get(state) or {}).get("kind") == "turned_down" or reason:
		kind = "turned_down"
	elif state in (shaped.get("terminal") or []):
		kind = "finished"
	else:
		kind = "forward"

	return {
		"doctype": row.reference_doctype,
		"name": row.reference_name,
		"title": title,
		"state": state,
		"tone": tones.get(state) if state else None,
		"kind": kind,
		"reason": reason,
		"derived": bool(derived),
		"by": row.owner,
		"by_name": get_fullname(row.owner),
		"at": str(row.creation),
	}


def _same_move(previous: dict | None, item: dict) -> bool:
	return (
		previous is not None
		and item["state"] is not None
		and not item["derived"]
		and not previous["derived"]
		and (previous["doctype"], previous["name"], previous["state"], previous["by"])
		== (item["doctype"], item["name"], item["state"], item["by"])
		and bool(previous["reason"]) != bool(item["reason"])
	)


def _announcements() -> list[dict]:
	"""Public, unexpired Notes, newest first.

	No owner filter is needed: Note.public and expire_notification_on are permlevel 1, and only
	System Manager holds permlevel-1 write on Note, so every public note was published by one.
	Public notes are readable by everyone (note.has_permission). `content` stays HTML --
	Note.validate already sanitises it, and the SPA sanitises it again.
	"""
	notes = frappe.get_all(
		"Note",
		filters={"public": 1},
		or_filters=[["expire_notification_on", "is", "not set"], ["expire_notification_on", ">=", now()]],
		fields=["name", "title", "content", "owner", "creation", "expire_notification_on"],
		order_by="creation desc",
		limit=5,
	)
	return [
		{
			"name": note.name,
			"title": note.title,
			"content": note.content or "",
			"by": note.owner,
			"by_name": get_fullname(note.owner),
			"at": str(note.creation),
			"expires": str(note.expire_notification_on) if note.expire_notification_on else None,
		}
		for note in notes
	]


# --- switch_targets ------------------------------------------------------------------------

# Roles every login carries: never the line that says who somebody is.
_GENERIC_ROLES = {"All", "Guest", "Desk User", "Employee", "Employee Self Service"}
SWITCH_LIMIT = 50


@frappe.whitelist(methods=["GET"])
def switch_targets(q: str | None = None) -> dict:
	"""Who the caller may switch to: enabled desk users other than themselves and Administrator.

	Refused unless the caller holds User "impersonate" -- frappe.core.doctype.user.user.impersonate
	asks exactly that before switching, so this list never offers a switch that would then fail.
	`label` is the person's Active Employee designation, else their first role that is not one every
	login carries, else "Employee" for someone who holds only that (the SPA's primaryRole reads the
	same way). Up to 50, by name; `q` narrows by name or login.
	"""
	if not frappe.has_permission("User", "impersonate"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	filters = [
		["enabled", "=", 1],
		["user_type", "=", "System User"],
		["name", "not in", ["Guest", "Administrator", frappe.session.user]],
	]
	or_filters = None
	text = (q or "").strip()
	if text:
		like = f"%{text}%"
		or_filters = [["full_name", "like", like], ["name", "like", like]]
	users = frappe.get_all(
		"User",
		filters=filters,
		or_filters=or_filters,
		fields=["name", "full_name", "user_image"],
		order_by="full_name asc",
		limit=SWITCH_LIMIT,
	)
	names = [u.name for u in users]
	designations = {}
	roles: dict[str, list[str]] = {}
	employees: set[str] = set()
	if names:
		for row in frappe.get_all(
			"Employee",
			filters={"user_id": ["in", names], "status": "Active"},
			fields=["user_id", "designation"],
		):
			if row.designation:
				designations.setdefault(row.user_id, row.designation)
		for row in frappe.get_all(
			"Has Role",
			filters={"parenttype": "User", "parent": ["in", names]},
			fields=["parent", "role"],
			order_by="idx asc",
		):
			if row.role not in _GENERIC_ROLES:
				roles.setdefault(row.parent, []).append(row.role)
			elif row.role == "Employee":
				employees.add(row.parent)
	return {
		"users": [
			{
				"user": u.name,
				"full_name": u.full_name or u.name,
				"label": designations.get(u.name)
				or (roles.get(u.name) or [None])[0]
				or ("Employee" if u.name in employees else None),
				"image": u.user_image or None,
			}
			for u in users
		]
	}

