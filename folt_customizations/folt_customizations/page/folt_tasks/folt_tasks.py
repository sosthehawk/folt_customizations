"""What is waiting for me, across every FoLT chain at once.

The Desk answers "what is in this doctype" very well and "what is waiting for me" not at all.
A Finance Officer at FoLT holds steps in five different chains -- checking a float, reviewing a
reimbursement list, reviewing a float retirement, forwarding a waiver, marking a float accounted
-- and the only way to find them was to open five list views and know which state to look for in
each. So the first thing anybody did every morning was the one thing the system would not do.

This is that list. Nothing in it is configured: the queues are derived from the active workflows
the same way everything else in this layer is, by asking which states have a transition out of
them whose `allowed` role is one the viewer holds. A chain added next month appears here on the
deploy that adds it.

FOUR BUCKETS, AND THE LINE BETWEEN THE FIRST TWO IS THE INTERESTING ONE. A document you raised
that is waiting for you to submit it is not an approval queue -- it is your own unfinished work,
and mixing the two makes the queue useless as a queue. That distinction is exactly the guard
notifications.py already needed in order to stop alerting an entire role about somebody's
unsubmitted draft, so it is asked of the same function: workflow.is_own_todo.

PERMISSIONS ARE NOT RE-IMPLEMENTED HERE. Every query goes through `frappe.get_list`, which
applies the doctype's own DocPerms, User Permissions and any permission query conditions. That
is the whole of why this file contains no permission check of its own: a role that cannot read
Salary Slip does not get Salary Slips in its queue because the query does not return them, not
because this module remembered to ask.
"""

import frappe
from frappe import _
from frappe.utils import date_diff, nowdate

from folt_customizations import workflow_shape
from folt_customizations.workflow import is_own_todo

BUCKETS = ("awaiting", "drafts", "approved", "archives")

# How many documents one bucket will return. A queue longer than this is not a queue any more,
# and the list view (one click away on every row's doctype) is the right tool for the whole set.
PAGE_LENGTH = 50


def pending_map() -> dict[str, dict[str, list[str]]]:
	"""{doctype: {state: [roles that can move a document out of it]}} across active workflows.

	Derived per request from the same `frappe.get_cached_doc("Workflow", ...)` loads
	workflow_access.add_turn_downs_to_bootinfo already makes on every boot -- nine cached reads.
	No cache of its own, and therefore no invalidation bug of its own.
	"""
	pending = {}
	for name in frappe.get_all("Workflow", filters={"is_active": 1}, pluck="name"):
		workflow = frappe.get_cached_doc("Workflow", name)
		shaped = workflow_shape.shape(workflow.document_type)
		if shaped:
			pending[workflow.document_type] = shaped["roles_by_state"]

	return pending


@frappe.whitelist()
def my_tasks(bucket: str = "awaiting", limit: int = PAGE_LENGTH) -> dict:
	"""One bucket's worth of rows, grouped by the step they are sitting on, plus all four counts.

	The counts come with every call so the sidebar can show them without four round trips; they
	are the cheap half (a count query per doctype) and the rows are the expensive half.
	"""
	if bucket not in BUCKETS:
		frappe.throw(_("{0} is not one of {1}.").format(bucket, ", ".join(BUCKETS)))

	limit = min(int(limit or PAGE_LENGTH), PAGE_LENGTH)
	roles = set(frappe.get_roles())

	# Permission is ASKED, not caught. `frappe.get_list` on a doctype the user cannot read calls
	# frappe.throw, and catching the resulting PermissionError is not enough: the message is
	# already on `frappe.local.message_log` by then, and frappe ships that to the browser with the
	# response whether or not anybody caught the exception. A Finance Officer opening this page got
	# three "Insufficient Permission" dialogs stacked over an empty queue -- their own tasks were
	# fine, the doctypes they hold no step in were not. has_permission answers the same question
	# and queues nothing.
	pending = {
		doctype: states_roles
		for doctype, states_roles in pending_map().items()
		if frappe.has_permission(doctype, "read")
	}

	rows = []
	counts = dict.fromkeys(BUCKETS, 0)

	for doctype, states_roles in pending.items():
		shaped = workflow_shape.shape(doctype)
		if not shaped:
			continue

		found = _for_doctype(doctype, shaped, states_roles, roles, bucket, limit)
		rows.extend(found["rows"])
		for key in BUCKETS:
			counts[key] += found["counts"][key]

	# Oldest first: the only sensible default for a queue, and the one that surfaces the document
	# somebody has been waiting on for three weeks rather than the one raised this morning.
	rows.sort(key=lambda row: row["modified"])

	return {"bucket": bucket, "counts": counts, "groups": _group(rows), "total": len(rows)}


def _for_doctype(doctype, shaped, states_roles, roles, bucket, limit) -> dict:
	"""One doctype's contribution: the requested bucket's rows, and every bucket's count."""
	state_field = shaped["state_field"]
	user = frappe.session.user

	mine = [state for state, movers in states_roles.items() if roles & set(movers)]
	submitted = {
		state for lane in shaped["lanes"] if lane["docstatus"] == 1 for state in lane["states"]
	}
	ended = set(shaped["terminal"]) | {
		state for state, info in shaped["off_path"].items() if info["kind"] == "turned_down"
	}

	fields = _fields(doctype, state_field)

	def query(filters, or_filters=None, page_length=0):
		return frappe.get_list(
			doctype,
			filters=filters,
			or_filters=or_filters,
			fields=fields,
			order_by="modified asc",
			limit_page_length=page_length or 0,
			ignore_ifnull=True,
		)

	# Documents I acted on, for the two backward-looking buckets. One query per doctype, and
	# precise, because apply_workflow files a Workflow Comment for every transition.
	def touched_by_me():
		return frappe.get_all(
			"Comment",
			filters={
				"reference_doctype": doctype,
				"comment_type": "Workflow",
				"owner": user,
			},
			pluck="reference_name",
			distinct=True,
		)

	buckets = {}

	# Awaiting: sitting in a state I can move it out of, and genuinely waiting on an approver
	# rather than on its own author. The second half is asked of the *owner*, not of the viewer:
	# somebody else's unfinished draft is not a task for me either, and a queue that lists every
	# draft on the site because I happen to hold the Employee role is a queue nobody reads.
	buckets["awaiting"] = [
		row
		for row in (query({state_field: ("in", mine), "docstatus": ("<", 2)}) if mine else [])
		if not is_own_todo(row.owner, states_roles.get(row.get(state_field)) or [])
	]

	# Drafts: mine and not yet submitted. Deliberately not "in the first state" -- a document
	# returned for correction is a draft again, and it is the one most in need of finding.
	buckets["drafts"] = query({"owner": user, "docstatus": 0})

	if bucket in ("approved", "archives"):
		# Documents I raised or moved. `filters` are ANDed and `or_filters` ORed among
		# themselves, so this reads as "in one of these states AND (mine or one I acted on)".
		involved = touched_by_me()
		or_filters = {"owner": user, "name": ("in", involved)} if involved else {"owner": user}

		buckets["approved"] = (
			query({state_field: ("in", sorted(submitted - ended))}, or_filters=or_filters)
			if submitted - ended
			else []
		)
		# Two ways to be archived -- an end state, or cancelled -- and a cancelled document in an
		# end state is both, so the union is deduplicated rather than concatenated.
		buckets["archives"] = _unique(
			(query({state_field: ("in", sorted(ended))}, or_filters=or_filters) if ended else [])
			+ query({"docstatus": 2}, or_filters=or_filters)
		)
	else:
		# Counted lazily: the two backward-looking buckets each cost two more queries per
		# doctype, and nobody is looking at their counts while reading their queue.
		buckets["approved"] = []
		buckets["archives"] = []

	counts = {key: len(rows) for key, rows in buckets.items()}
	rows = [_row(doctype, shaped, states_roles, row, state_field) for row in buckets[bucket][:limit]]

	return {"rows": rows, "counts": counts}


def _unique(rows: list) -> list:
	seen = set()
	out = []
	for row in rows:
		if row.name in seen:
			continue
		seen.add(row.name)
		out.append(row)
	return out


def _fields(doctype: str, state_field: str) -> list[str]:
	"""The columns a queue row needs, taken from what the doctype says about itself.

	`title_field` is frappe's own answer to "what is this document called", and a Currency field
	the doctype marked `in_list_view` is its own answer to "which number matters in a list". Both
	are read rather than declared here, so a new module's queue is legible with nothing added.

	Deliberately NO fallback to "the first Currency field": on Salary Slip that is `hour_rate` and
	on Purchase Order it is `base_total`, so a fallback would confidently show the wrong number.
	Showing none is the honest answer where the doctype has not said.
	"""
	meta = frappe.get_meta(doctype)
	fields = ["name", "owner", "modified", "docstatus", state_field]

	if meta.title_field and meta.title_field not in fields:
		fields.append(meta.title_field)

	amount = next(
		(f.fieldname for f in meta.fields if f.fieldtype == "Currency" and f.in_list_view), None
	)
	if amount:
		fields.append(amount)

	return fields


def _row(doctype, shaped, states_roles, row, state_field) -> dict:
	state = row.get(state_field)
	meta = frappe.get_meta(doctype)
	placed = workflow_shape.locate(shaped, state)
	amount = next(
		(f.fieldname for f in meta.fields if f.fieldtype == "Currency" and f.in_list_view), None
	)

	return {
		"doctype": doctype,
		"name": row.name,
		"title": (meta.title_field and row.get(meta.title_field)) or row.name,
		"state": state,
		"lane": placed["lane"],
		"of": placed["of"],
		"step_label": next(
			(step["label"] for step in placed["steps"] if step["status"] == "current"), state
		),
		"waiting_on": states_roles.get(state) or [],
		"owner": row.owner,
		"owner_name": frappe.get_cached_value("User", row.owner, "full_name") or row.owner,
		"modified": str(row.modified),
		"age_days": date_diff(nowdate(), row.modified),
		"amount": row.get(amount) if amount else None,
		"currency_field": amount,
	}


def _group(rows: list[dict]) -> list[dict]:
	"""Group by the step, not by the doctype.

	Which chain a task belongs to matters less than what is being asked of you: "three floats to
	check" is one job done three times, while a list sorted by doctype hides that behind headings.
	The group key comes straight from the shape, so it needs no metadata of its own.
	"""
	grouped: dict[tuple, dict] = {}
	for row in rows:
		key = (row["doctype"], row["lane"])
		group = grouped.setdefault(
			key,
			{
				"key": f"{row['doctype']}::{row['state']}",
				"doctype": row["doctype"],
				"step_label": row["step_label"],
				"lane": row["lane"],
				"of": row["of"],
				"waiting_on": row["waiting_on"],
				"rows": [],
			},
		)
		group["rows"].append(row)

	return sorted(grouped.values(), key=lambda g: (g["doctype"], g["lane"] if g["lane"] is not None else 99))
