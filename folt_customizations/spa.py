"""The server half of /folt: one read model per screen, and the only write path the SPA has.

THE SPA OWNS A PATCH, NEVER A DOCUMENT. The Desk posts whole documents and gets away with it
because frappe's form keeps every row and every hidden field in step; a lighter client that did
the same would delete every child row it had not loaded (frappe removes rows missing from a save),
silently drop rows whose names it invented, and re-send a committee colleague's score as a new row
that enforce_self_scoring then blames on the sender. So nothing here accepts a document. `save`,
`act` and `create` accept a patch -- scalar values plus explicit row operations -- which is applied
to a document loaded from the database, against step_forms.STEP_FORMS, and refused field by field
with the field's name in the message when it is not what the step takes.

ONE REQUEST PER STEP. "Save & Approve" is a single call. It has to be: frappe's apply_workflow
opens with `load_from_db()` and discards any edit not already saved (frappe/model/workflow.py),
and workflow_access keys a turn-down's reason on the document's `modified`, so any save between
parking a reason and taking the action loses it. `act` therefore saves, parks the reason against
the version it just wrote, and transitions -- in that order, in one transaction, so a refusal at
any point rolls back all of it. Neither frappe's save nor apply_workflow commits.

WHAT IS NOT DONE HERE. apply_workflow is called verbatim, never re-implemented: it re-checks the
transition's role and self-approval, applies `update_field`, saves or submits and writes the
Workflow comment the timeline reads, so an action taken here is indistinguishable from one taken
in the Desk. Every guard -- the custodian, the reason, every controller rule -- stays where it is.
The step's field values ride on a transition only when the actor holds the state's custodian role;
where they do not (the Executive Director approving a float out of the Finance Officer's `Checked`)
the step is act-only and a value sent with it is refused rather than dropped.
"""

import json
from urllib.parse import quote

import frappe
from frappe import _
from frappe.model.workflow import (
	apply_workflow,
	get_transitions,
	get_workflow,
	get_workflow_name,
	has_approval_access,
)
from frappe.utils import cint, flt, getdate

from folt_customizations import activity_chain, procurement_chain, step_forms, workflow_shape
from folt_customizations.document_guide import _documents, get_guide, guide_map
from folt_customizations.folt_customizations.page.folt_tasks.folt_tasks import _fields, _row
from folt_customizations.float_lifecycle import FUNDED_FLOAT_STATES
from folt_customizations.workflow_access import forget_held_reason, is_turn_down, park_reason

ADMINISTRATOR = "Administrator"


# --- reading ------------------------------------------------------------------------------------


@frappe.whitelist()
def catalogue() -> list[dict]:
	"""The workflows this user can read: their lanes, how many documents sit in each state, and
	whether they can start one here. The Workflows screen and the state chips' tones."""
	out = []
	for doctype, guided in guide_map(only_permitted=True).items():
		state_field = guided["state_field"]
		counts = {
			row.get(state_field): row.get("COUNT(*)")
			for row in frappe.get_list(doctype, fields=[state_field, {"COUNT": "*"}], group_by=state_field)
		}
		out.append(
			{
				"doctype": doctype,
				"label": _(doctype),
				"lanes": guided["lanes"],
				"off_path": guided["off_path"],
				"chain": guided["chain"],
				"counts": counts,
				"tones": state_tones(doctype),
				"can_create": doctype in step_forms.CREATE_FORMS and bool(frappe.has_permission(doctype, "create")),
			}
		)
	# Supplier Quotation has no workflow and so no guide, but the procurement chain starts at a
	# submitted bid, and a buyer needs somewhere in the SPA to find one.
	if frappe.has_permission("Supplier Quotation", "read"):
		by_status = {
			row.get("docstatus"): row.get("COUNT(*)")
			for row in frappe.get_list("Supplier Quotation", fields=["docstatus", {"COUNT": "*"}], group_by="docstatus")
		}
		out.append(
			{
				"doctype": "Supplier Quotation",
				"label": _("Supplier Quotation"),
				"lanes": [],
				"off_path": {},
				"chain": None,
				"counts": {"Draft": by_status.get(0, 0), "Submitted": by_status.get(1, 0), "Cancelled": by_status.get(2, 0)},
				"tones": {"Draft": "plain", "Submitted": "ok", "Cancelled": "danger"},
				"can_create": False,
			}
		)
	return out


@frappe.whitelist()
def documents(doctype: str, state: str | None = None, query: str = "", start: int = 0, limit: int = 20) -> dict:
	"""One workflow's documents, newest first, in the same row shape as My Tasks."""
	_serves(doctype)
	if not frappe.has_permission(doctype, "read"):
		return {"rows": [], "more": False}

	limit = min(cint(limit) or 20, 50)
	start = cint(start)
	shaped = workflow_shape.shape(doctype)
	state_field = shaped["state_field"] if shaped else None
	meta = frappe.get_meta(doctype)

	filters = {}
	if state and state_field:
		filters[state_field] = state
	elif state and not shaped:
		filters["docstatus"] = {"Draft": 0, "Submitted": 1, "Cancelled": 2}.get(state, 0)

	or_filters = None
	if query:
		like = f"%{query.strip()}%"
		or_filters = [["name", "like", like]]
		if meta.title_field:
			or_filters.append([meta.title_field, "like", like])

	if shaped:
		fields = _fields(doctype, state_field)
	else:
		fields = ["name", "owner", "modified", "docstatus"] + ([meta.title_field] if meta.title_field else [])

	found = frappe.get_list(
		doctype,
		filters=filters,
		or_filters=or_filters,
		fields=fields,
		order_by="modified desc",
		limit_start=start,
		limit_page_length=limit + 1,
	)
	rows = found[:limit]
	if shaped:
		shaped_rows = [_row(doctype, shaped, shaped["roles_by_state"], row, state_field) for row in rows]
	else:
		shaped_rows = [_plain_row(doctype, meta, row) for row in rows]
	return {"rows": shaped_rows, "more": len(found) > limit}


def _plain_row(doctype, meta, row) -> dict:
	from frappe.utils import date_diff, nowdate

	return {
		"doctype": doctype,
		"name": row.name,
		"title": (meta.title_field and row.get(meta.title_field)) or row.name,
		"state": {0: "Draft", 1: "Submitted", 2: "Cancelled"}[row.docstatus],
		"lane": None,
		"of": 0,
		"step_label": None,
		"waiting_on": [],
		"owner": row.owner,
		"owner_name": frappe.get_cached_value("User", row.owner, "full_name") or row.owner,
		"modified": str(row.modified),
		"age_days": date_diff(nowdate(), row.modified),
		"amount": None,
		"currency_field": None,
	}


@frappe.whitelist()
def document(doctype: str, name: str) -> dict:
	"""Everything the document screen shows, in one call.

	`guide` is document_guide.get_guide unchanged -- the Desk's own read model, so the tracker,
	timeline and checklist here and in the Desk cannot disagree. What is added is what the Desk
	still works out in the browser: which actions THIS user can take (`actions`, with frappe's own
	self-approval rule applied), what this step lets them edit (`form`), and the read-only details.
	"""
	_serves(doctype)
	frappe.has_permission(doctype, doc=name, throw=True)
	doc = frappe.get_doc(doctype, name)

	workflow_name = get_workflow_name(doctype)
	guide = get_guide(doctype, name) if workflow_name else _unguided(doc)
	state = guide.get("state")

	return {
		"doctype": doctype,
		"name": doc.name,
		"title": _title_of(doc),
		"modified": str(doc.modified),
		"docstatus": doc.docstatus,
		"owner": doc.owner,
		"owner_name": frappe.get_cached_value("User", doc.owner, "full_name") or doc.owner,
		"state": state,
		"tones": state_tones(doctype),
		"guide": guide,
		"actions": actions_for(doc) if workflow_name else [],
		"form": form_for(doc) if workflow_name else _no_form(_("This document has no approval workflow.")),
		"summary": summary_for(doc),
		"context": context_for(doc),
		"desk_url": f"/desk/{frappe.scrub(doctype).replace('_', '-')}/{quote(doc.name)}",
	}


def _unguided(doc) -> dict:
	"""The part of a guide a document with no workflow still has: its hand-offs and its route."""
	state = procurement_chain._state(doc)
	return {
		"doctype": doc.doctype,
		"name": doc.name,
		"state": state,
		"docstatus": doc.docstatus,
		"steps": [],
		"lane": None,
		"of": 0,
		"at_optional": None,
		"off_path": None,
		"chain": None,
		"handoffs": procurement_chain.handoffs_for(doc),
		"note": procurement_chain.route_note(doc),
		"waiting_for": {"roles": [], "approvers": [], "unassigned": False},
		"can_act": False,
		"timeline": [],
		"documents": [],
		"blocked_by": [],
		"rejection_reason": None,
	}


def state_tones(doctype: str) -> dict[str, str]:
	"""How each state should read -- derived from the workflow's shape, not from its wording.

	Keying on the words goes wrong here at once: FoLT's Draft is styled Danger in
	workflow_state.json, and Checked, Disbursed, Accounted, Verified and Settled share no keyword
	with anything. The shape already knows: a state documents are turned down into, or detour
	through, is a refusal; a submitted end state is done; the first state is somebody's draft;
	anything else unsubmitted is waiting on a person; anything submitted but not finished is
	moving on its own (a float being spent, a list being paid).
	"""
	if not get_workflow_name(doctype):
		return {}
	workflow = get_workflow(doctype)
	shaped = workflow_shape.shape(doctype) or {}
	off_path = shaped.get("off_path") or {}
	terminal = set(shaped.get("terminal") or [])
	first = workflow.states[0].state if workflow.states else None

	tones = {}
	for row in workflow.states:
		state = row.state
		submitted = int(row.doc_status or 0) == 1
		if state in step_forms.STATE_TONE_OVERRIDES:
			tone = step_forms.STATE_TONE_OVERRIDES[state]
		elif (off_path.get(state) or {}).get("kind") in ("turned_down", "detour"):
			tone = "danger"
		elif submitted and state in terminal:
			tone = "ok"
		elif state == first:
			tone = "plain"
		elif submitted:
			tone = "info"
		else:
			tone = "warn"
		tones[state] = tone
	return tones


def actions_for(doc) -> list[dict]:
	"""The transitions this user can take now, each saying what it needs.

	frappe's get_transitions filters by role and condition but NOT by self-approval -- that is
	checked only inside apply_workflow, so the Desk has kept a JavaScript copy of the rule
	(folt_guide.js may_self_approve) to hide buttons that would throw. Here it is frappe's own
	predicate, called on the server, so the SPA has no copy to drift.
	"""
	if doc.docstatus == 2 or doc.is_new():
		return []
	workflow = get_workflow(doc.doctype)
	state = doc.get(workflow.workflow_state_field)
	user = frappe.session.user
	evidence = _documents(doc, doc.doctype)
	next_status = {row.state: int(row.doc_status or 0) for row in workflow.states}

	actions = []
	for transition in get_transitions(doc, workflow):
		if not has_approval_access(user, doc, transition):
			continue
		turn_down = is_turn_down(workflow, transition.state, transition.next_state)
		# The ledger moves a float into these on its own (float_lifecycle) -- a Payment Entry makes
		# it Disbursed, a retirement makes it Accounted -- and the manual transitions exist to correct
		# it, so they are offered as corrections rather than as the next step. FUNDED_FLOAT_STATES,
		# not DERIVED_STATES: the latter includes Approved, which is the Executive Director's real
		# decision and not the ledger's.
		correction = doc.doctype == "Employee Advance" and transition.next_state in FUNDED_FLOAT_STATES
		actions.append(
			{
				"action": transition.action,
				"label": _(transition.action),
				"next_state": transition.next_state,
				"kind": "turn_down" if turn_down else ("correction" if correction else "forward"),
				"needs_reason": turn_down,
				"submits": doc.docstatus == 0 and next_status.get(transition.next_state) == 1,
				"blocks": [
					row["label"]
					for row in evidence
					if not row["attached"] and transition.next_state in row["required_at"]
				],
			}
		)

	primary = next((a for a in actions if a["kind"] == "forward"), None)
	for action in actions:
		action["primary"] = action is primary
	return actions


def form_for(doc) -> dict:
	"""What this step lets the reader edit, with current values -- or why it lets them edit nothing."""
	workflow = get_workflow(doc.doctype)
	state = doc.get(workflow.workflow_state_field)
	entry = step_forms.STEP_FORMS.get((doc.doctype, state))
	custodians = _custodians(workflow, state)
	if not entry:
		return _no_form(None, custodians=custodians)

	why_not = _why_not_editable(doc, custodians)
	return _form_payload(doc, entry, editable=not why_not, why_not=why_not, custodians=custodians)


def _no_form(why: str | None, custodians=None) -> dict:
	return {
		"editable": False,
		"act_only": True,
		"why_not": why,
		"custodians": custodians or [],
		"fields": [],
		"virtual": [],
		"tables": [],
		"show_if": {},
		"tools": [],
	}


def _form_payload(doc, entry, editable: bool, why_not: str | None, custodians: list[str]) -> dict:
	meta = doc.meta
	titles = _Titles()
	tables = []
	for fieldname, spec in entry.tables.items():
		field = meta.get_field(fieldname)
		child = frappe.get_meta(field.options)
		rows = []
		for row in doc.get(fieldname) or []:
			mine = spec.rows != "own" or row.get(spec.own_field) == frappe.session.user
			rows.append(
				{
					"name": row.name,
					"idx": row.idx,
					"values": {c: _plain(row.get(c)) for c in [*spec.show, *spec.columns]},
					"display": {
						c: titles.get(child.get_field(c).options, row.get(c))
						for c in [*spec.show, *spec.columns]
						if child.get_field(c).fieldtype == "Link" and row.get(c)
					},
					"editable": editable and mine,
				}
			)
		tables.append(
			{
				"fieldname": fieldname,
				"label": _(field.label),
				"columns": [_field_spec(child, c, None, spec.required, titles) for c in spec.columns],
				"show": [_field_spec(child, c, None, (), titles) for c in spec.show],
				"rows": rows,
				"add": editable and spec.add,
				"remove": editable and spec.remove,
				"own_rows": spec.rows == "own",
				"new_row": spec.new_row,
			}
		)

	return {
		"editable": editable,
		"act_only": False,
		"why_not": why_not,
		"custodians": custodians,
		"fields": [_field_spec(meta, f, doc, entry.required, titles) for f in entry.fields],
		"virtual": [_virtual_spec(doc, name) for name in entry.virtual],
		"tables": tables,
		"show_if": entry.show_if,
		"tools": entry.tools if editable else [],
	}


def summary_for(doc) -> list[dict]:
	"""The read-only details, section by section, skipping anything this reader may not see."""
	meta = doc.meta
	titles = _Titles()
	currency = _currency(doc)
	sections = []
	for label, fields in step_forms.SUMMARY.get(doc.doctype, []):
		if isinstance(fields, dict):
			field = meta.get_field(fields["table"])
			if not field or not doc.has_permlevel_access_to(fields["table"]):
				continue
			child = frappe.get_meta(field.options)
			columns = [_field_spec(child, c, None, (), titles) for c in fields["columns"] if child.get_field(c)]
			rows = [
				{
					"name": row.name,
					"values": {c["fieldname"]: _plain(row.get(c["fieldname"])) for c in columns},
					"display": {
						c["fieldname"]: titles.get(c.get("options"), row.get(c["fieldname"]))
						for c in columns
						if c["fieldtype"] == "Link" and row.get(c["fieldname"])
					},
				}
				for row in doc.get(fields["table"]) or []
			]
			sections.append({"label": _(label), "table": {"columns": columns, "rows": rows}, "currency": currency})
			continue
		shown = [
			_field_spec(meta, f, doc, (), titles)
			for f in fields
			if meta.get_field(f) and doc.has_permlevel_access_to(f)
		]
		if shown:
			sections.append({"label": _(label), "fields": shown, "currency": currency})
	return sections


def context_for(doc) -> dict:
	"""Facts from OTHER documents this screen needs and the reader may not be able to open.

	The Finance Manager approving an order has no read on the evaluation or waiver behind it --
	the gate in purchase_order.py reads them on their behalf -- so the order's screen carries the
	few facts the approver is being asked to rely on, and nothing else from those documents.
	"""
	context = {}
	if doc.doctype == "Purchase Order":
		authority = []
		if doc.get("folt_committee_evaluation"):
			row = frappe.db.get_value(
				"Procurement Committee Evaluation",
				doc.folt_committee_evaluation,
				["name", "workflow_state", "recommended_supplier", "request_for_quotation", "modified"],
				as_dict=True,
			)
			if row:
				authority.append({"route": _("Competitive bidding"), "doctype": "Procurement Committee Evaluation", **row})
		if doc.get("folt_waiver_request"):
			row = frappe.db.get_value(
				"Derogation Waiver Request",
				doc.folt_waiver_request,
				["name", "workflow_state", "supplier", "estimated_value", "modified"],
				as_dict=True,
			)
			if row:
				authority.append({"route": _("Single sourcing (waiver)"), "doctype": "Derogation Waiver Request", **row})
		context["authority"] = [{k: _plain(v) for k, v in a.items()} for a in authority]
	elif doc.doctype == "Procurement Committee Evaluation" and doc.get("request_for_quotation"):
		from folt_customizations.procurement import rfq_quotations

		context["bids"] = [{k: _plain(v) for k, v in bid.items()} for bid in rfq_quotations(doc.request_for_quotation)]
		context["me_on_committee"] = any(row.member == frappe.session.user for row in doc.members or [])
	elif doc.doctype == "Employee Advance":
		paid, claimed, returned = flt(doc.paid_amount), flt(doc.claimed_amount), flt(doc.return_amount)
		context["float"] = {"paid": paid, "claimed": claimed, "returned": returned, "balance": paid - claimed - returned}
	elif doc.doctype == "Participant Reimbursement List" and doc.get("employee_advance"):
		row = frappe.db.get_value(
			"Employee Advance",
			doc.employee_advance,
			["name", "employee_name", "paid_amount", "claimed_amount", "workflow_state"],
			as_dict=True,
		)
		context["float"] = {k: _plain(v) for k, v in (row or {}).items()}
	return context


# --- writing ------------------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def save(doctype: str, name: str, values: str | dict | None = None, modified: str | None = None) -> dict:
	"""Edit a document without moving it: the step's fields, as a patch."""
	doc = _load(doctype, name, modified)
	entry = _editable_step(doc)
	_apply_patch(doc, entry, _parse(values))
	doc.save()
	return document(doctype, doc.name)


@frappe.whitelist(methods=["POST"])
def act(
	doctype: str,
	name: str,
	action: str,
	reason: str | None = None,
	values: str | dict | None = None,
	modified: str | None = None,
) -> dict:
	"""Take a workflow action -- with its reason, and the step's edits if the actor may make them.

	The order below is forced; see the module docstring.
	"""
	doc = _load(doctype, name, modified)
	workflow = get_workflow(doctype)
	state = doc.get(workflow.workflow_state_field)

	transition = next((t for t in get_transitions(doc, workflow) if t.action == action), None)
	if not transition:
		frappe.throw(
			_("{0} cannot be taken on {1} at {2} by you.").format(
				frappe.bold(_(action)), frappe.bold(doc.name), frappe.bold(_(state))
			),
			title=_("Not an action you can take"),
		)
	if not has_approval_access(frappe.session.user, doc, transition):
		frappe.throw(
			_("You raised {0}, so {1} has to be taken by somebody else: this step does not let a document's author approve it.").format(
				frappe.bold(doc.name), frappe.bold(_(action))
			),
			title=_("Self approval is not allowed"),
		)

	turn_down = is_turn_down(workflow, transition.state, transition.next_state)
	reason = (reason or "").strip()
	# Refused before anything is written: a turn-down with no reason must leave the document
	# exactly as it was, not half-edited.
	if turn_down and not reason:
		frappe.throw(
			_("Say why: {0} cannot be sent to {1} without a reason, because somebody has to act on it.").format(
				frappe.bold(doc.name), frappe.bold(_(transition.next_state))
			),
			title=_("A reason is required"),
		)

	patch = _parse(values)
	if patch:
		entry = _editable_step(doc, taking=action)
		_apply_patch(doc, entry, patch)
		doc.save()

	parked = park_reason(doctype, doc.name, reason) if turn_down else None
	try:
		# By name, not the in-memory document: apply_workflow reloads from the database anyway,
		# and handing it a dict makes that explicit.
		apply_workflow({"doctype": doctype, "name": doc.name}, action)
	finally:
		if parked is not None:
			forget_held_reason(doctype, doc.name, parked)

	return document(doctype, doc.name)


@frappe.whitelist()
def new_form(doctype: str) -> dict:
	"""The blank form for a chain starter, with the defaults a new document would get."""
	entry = step_forms.CREATE_FORMS.get(doctype)
	if not entry:
		frappe.throw(_("{0} is not raised from here. It is created by the step before it.").format(_(doctype)))
	frappe.has_permission(doctype, "create", throw=True)

	doc = frappe.new_doc(doctype)
	if doctype == "Derogation Waiver Request" and not doc.get("organisation_project_name"):
		doc.organisation_project_name = procurement_chain.get_waiver_heading()
	payload = _form_payload(doc, entry, editable=True, why_not=None, custodians=[])
	payload["doctype"] = doctype
	payload["label"] = _(doctype)
	return payload


@frappe.whitelist(methods=["POST"])
def create(doctype: str, values: str | dict | None = None) -> dict:
	"""Raise a chain starter from a patch. Everything the controller defaults, it defaults."""
	entry = step_forms.CREATE_FORMS.get(doctype)
	if not entry:
		frappe.throw(_("{0} is not raised from here. It is created by the step before it.").format(_(doctype)))
	frappe.has_permission(doctype, "create", throw=True)

	doc = frappe.new_doc(doctype)
	_apply_patch(doc, entry, _parse(values), state=_("a new document"))
	if doctype == "Derogation Waiver Request" and not doc.get("organisation_project_name"):
		doc.organisation_project_name = procurement_chain.get_waiver_heading(doc.get("project"))
	doc.insert()
	return {"name": doc.name, "document": document(doctype, doc.name)}


@frappe.whitelist(methods=["POST"])
def handoff(doctype: str, name: str, target: str, extra: str | dict | None = None) -> dict:
	"""Create the next document in a chain, filled in from this one.

	A thin router onto the existing makers in activity_chain and procurement_chain, which keep
	every rule (ready state, exclusivity, what is copied). Two things are added: the target is
	checked against the declared hand-offs, so this cannot call an arbitrary method, and create
	permission is asked up front -- activity_chain's buttons never asked, so a role that could not
	create the target got a button that threw.
	"""
	_serves(doctype)
	declared = [
		(module, row)
		for module in (activity_chain, procurement_chain)
		for row in module.HANDOFFS.get(doctype, [])
		if row.target == target
	]
	if not declared:
		frappe.throw(_("{0} does not lead to a {1}.").format(_(doctype), _(target)))
	module, row = declared[0]
	frappe.has_permission(target, "create", throw=True)

	kwargs = {row.arg: name}
	extra = _parse(extra)
	if target == "Participant Reimbursement List" and extra.get("employee_advance"):
		kwargs["employee_advance"] = extra["employee_advance"]

	result = getattr(module, row.method)(**kwargs)

	if isinstance(result, dict) and result.get("needs_float"):
		return {
			"needs_float": True,
			"activity": result.get("activity"),
			"floats": [{k: _plain(v) for k, v in f.items()} for f in result.get("floats") or []],
		}

	new_name = result if isinstance(result, str) else (result or {}).get("name")
	return {"doctype": target, "name": new_name, "label": _(row.label), "lines": _handoff_lines(target, result)}


def _handoff_lines(target: str, result) -> list[str]:
	"""What the hand-off found and what is left -- the same sentences the Desk shows
	(folt_guide.js folt.chain.report), so the two surfaces tell the next person the same thing."""
	if not isinstance(result, dict):
		return []
	lines = []
	if "added" in result:
		lines.append(_("{0} payees pulled from the register").format(result["added"]))
		if result.get("skipped_ineligible"):
			lines.append(_("{0} attendees skipped as not eligible by category").format(result["skipped_ineligible"]))
		if result.get("no_rate"):
			lines.append(_("No rate schedule applies to this activity — each amount has to be entered and justified."))
	if "bids" in result:
		lines.append(_("{0} bid(s) received against {1}.").format(result["bids"], result.get("rfq")))
		lines.append(_("Add the committee members: each of them gets a row to score every bid, and nobody can fill in anybody else's."))
	if "to_write" in result:
		lines.append(
			_("Filled in from the bid: {0}, {1} and the lines quoted for.").format(
				result.get("supplier"), frappe.utils.fmt_money(result.get("value"), currency=result.get("currency"))
			)
		)
		lines.append(_("Still to write: {0}.").format(", ".join(result["to_write"])))
	if "total" in result:
		lines.append(
			_("{0} to {1}, from quotation {2}.").format(
				frappe.utils.fmt_money(result["total"], currency=result.get("currency")),
				result.get("supplier"),
				result.get("quotation"),
			)
		)
		lines.append(_("Check Required By before sending it for approval."))
	if "spent" in result:
		lines.append(
			_("{0} accounted for against a float of {1}. Balance {2}.").format(
				frappe.utils.fmt_money(result["spent"]),
				frappe.utils.fmt_money(result.get("float_paid")),
				frappe.utils.fmt_money(result.get("balance")),
			)
		)
		lines.append(_("Add any other receipts — fuel, transaction charges — as further rows, in the Desk."))
		# make_float_retirement makes its caller the claim's owner, and Review & Forward refuses
		# self-approval: a Finance Officer who raises a retirement cannot forward it themselves.
		if "Finance Officer" in frappe.get_roles():
			lines.append(_("You raised this claim, so another Finance Officer has to review it: the author cannot forward their own."))
	return lines


@frappe.whitelist(methods=["POST"])
def attach(doctype: str, name: str, fieldname: str, modified: str | None = None) -> dict:
	"""Upload a file into one of this step's Attach fields, and set the field, in one transaction.

	Every gate is asked BEFORE the file is stored. frappe's own upload_file checks only write
	permission and does not set the field -- a lone upload would leave an orphaned File and an
	empty field, and it would not ask whether this step, or this person, may touch the field.
	"""
	doc = _load(doctype, name, modified)
	entry = _editable_step(doc)
	field = doc.meta.get_field(fieldname)
	if fieldname not in entry.fields or not field or field.fieldtype not in ("Attach", "Attach Image"):
		frappe.throw(
			_("{0} is not an attachment this step takes.").format(frappe.bold(fieldname)),
			title=_("Not part of this step"),
		)
	_guard_field(doc, field, fieldname, _step_label(doc))

	upload = (frappe.request.files or {}).get("file") if frappe.request else None
	if not upload:
		frappe.throw(_("No file was sent."))

	stored = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": upload.filename,
			"attached_to_doctype": doctype,
			"attached_to_name": doc.name,
			"attached_to_field": fieldname,
			# Signed sheets carry participants' names, phone numbers and signatures.
			"is_private": 1,
			"content": upload.stream.read(),
		}
	)
	stored.save()
	doc.set(fieldname, stored.file_url)
	doc.save()
	return document(doctype, doc.name)


# --- link pickers -------------------------------------------------------------------------------


@frappe.whitelist()
def options(doctype: str, fieldname: str, query: str = "", context: str | dict | None = None, limit: int = 20) -> dict:
	"""Choices for one field of one step form: {allowed, reason, options: [{value, label, hint}]}.

	Never a bare 403 halfway through a form. frappe's search_link throws for a role that cannot
	read the target -- the Head of Finance asking for a Donor, the Executive Director for a Project
	-- so the permission question is asked first and answered in words the form can show beside
	the field. Scoped to fields a step form actually offers, so this is not a way to enumerate any
	link on any doctype.
	"""
	_serves(doctype)
	context = _parse(context)
	limit = min(cint(limit) or 20, 50)
	# Not `table, _, column`: that would shadow gettext for the rest of this function.
	table, _dot, column = fieldname.partition(".")
	if not _offered(doctype, table if column else fieldname, column or None):
		frappe.throw(_("{0} is not a field any step form offers.").format(fieldname))

	meta = frappe.get_meta(doctype)
	if column:
		parent_field = meta.get_field(table)
		field = frappe.get_meta(parent_field.options).get_field(column)
	else:
		field = meta.get_field(fieldname)
	if not field or field.fieldtype != "Link":
		return {"allowed": False, "reason": _("Not a link field."), "options": []}
	target = field.options

	if not (frappe.has_permission(target, "select") or frappe.has_permission(target, "read")):
		return {
			"allowed": False,
			"reason": _("You cannot pick from the {0} list. Ask an administrator, or fill this in the Desk.").format(_(target)),
			"options": [],
		}

	custom = _custom_source(doctype, column or fieldname, target, query, context, limit)
	if custom is not None:
		return {"allowed": True, "reason": None, "options": custom}

	return {"allowed": True, "reason": None, "options": _generic_options(target, field, query, context, limit)}


def _custom_source(doctype, fieldname, target, query, context, limit):
	"""The four link queries FoLT already wrote for these fields, reused rather than restated."""
	txt = query or ""
	if doctype == "Participant Reimbursement List" and fieldname == "employee_advance":
		from folt_customizations.folt_customizations.doctype.participant_reimbursement_list.participant_reimbursement_list import (
			get_payable_floats,
		)

		filters = {"folt_project": context["activity"]} if context.get("activity") else {}
		return _tuples(get_payable_floats(target, txt, "name", 0, limit, filters))
	if doctype == "Participant Reimbursement List" and fieldname == "attendance_reference":
		from folt_customizations.folt_customizations.doctype.activity_participant_list.activity_participant_list import (
			get_verified_registers,
		)

		filters = {"activity": context["activity"]} if context.get("activity") else {}
		return _tuples(get_verified_registers(target, txt, "name", 0, limit, filters))
	if doctype == "Procurement Committee Evaluation" and fieldname == "recommended_supplier_quotation":
		# Only bids in this competition, and only submitted ones: a draft bid can be recommended and
		# approved, and then erpnext's mapper refuses to order it.
		name = context.get("name")
		if not name:
			return []
		frappe.has_permission(doctype, doc=name, throw=True)
		quotations = sorted(
			{
				row.supplier_quotation
				for row in frappe.get_doc(doctype, name).quotation_scores or []
				if row.supplier_quotation
			}
		)
		rows = frappe.get_all(
			"Supplier Quotation",
			filters={"name": ("in", quotations), "docstatus": 1},
			fields=["name", "supplier_name", "grand_total", "currency"],
		)
		return [
			{"value": r.name, "label": r.supplier_name or r.name, "hint": frappe.utils.fmt_money(r.grand_total, currency=r.currency)}
			for r in rows
			if not txt or txt.lower() in (r.name + (r.supplier_name or "")).lower()
		]
	return None


def _tuples(rows) -> list[dict]:
	out = []
	for row in rows or []:
		values = list(row)
		out.append(
			{
				"value": values[0],
				"label": str(values[1]) if len(values) > 1 and values[1] else values[0],
				"hint": " · ".join(str(v) for v in values[2:] if v not in (None, "")),
			}
		)
	return out


def _generic_options(target, field, query, context, limit) -> list[dict]:
	meta = frappe.get_meta(target)
	title = meta.title_field if meta.title_field and meta.get_field(meta.title_field) else None
	if target == "User":
		title = "full_name"
	hint = next(
		(f for f in (meta.search_fields or "").split(",") if f.strip() and f.strip() != title and meta.get_field(f.strip())),
		None,
	)
	hint = hint.strip() if hint else None

	filters = _default_filters(target, meta)
	filters.update(_link_filters(field, context))

	fields = ["name"] + ([title] if title else []) + ([hint] if hint else [])
	extra = _PREFILL.get(target)
	if extra:
		fields += [f for f in extra if f not in fields]

	or_filters = None
	if query:
		like = f"%{query.strip()}%"
		or_filters = [["name", "like", like]] + ([[title, "like", like]] if title else [])

	rows = frappe.get_list(
		target,
		filters=filters,
		or_filters=or_filters,
		fields=fields,
		order_by=f"{title or 'name'} asc",
		limit_page_length=limit,
	)
	out = []
	for row in rows:
		option = {
			"value": row.name,
			"label": str(row.get(title) or row.name) if title else row.name,
			"hint": str(row.get(hint)) if hint and row.get(hint) else "",
		}
		if extra:
			option["extra"] = {f: _plain(row.get(f)) for f in extra}
		out.append(option)
	return out


# Picking a known participant fills their profile in, as the Desk form does
# (activity_participant_list.js), so a returning attendee is not typed out again.
_PREFILL = {
	"FoLT Participant": ("participant_name", "mobile_number", "id_number", "location", "gender", "is_pwd", "photo_consent"),
}


def _default_filters(target: str, meta) -> dict:
	"""The obvious exclusions a Desk picker makes -- disabled records, group nodes, item
	templates -- written out because get_list makes none of them."""
	filters = {}
	if meta.get_field("disabled"):
		filters["disabled"] = 0
	if meta.get_field("is_group"):
		filters["is_group"] = 0
	if target == "User":
		filters.update({"enabled": 1, "user_type": "System User"})
	if target == "Employee":
		filters["status"] = "Active"
	if target == "Project":
		# `is_active` is a Select of Yes/No on Project, not a Check: filtering it with 1 matched
		# nothing and the picker just looked empty (the Gosolar lesson). Status is unambiguous.
		filters["status"] = "Open"
	if target == "FoLT Participant" and meta.get_field("is_active"):
		filters["is_active"] = 1
	return filters


def _link_filters(field, context: dict) -> dict:
	"""A field's own `link_filters`, with `eval:doc.x` resolved from the form's current values."""
	raw = field.get("link_filters")
	if not raw:
		return {}
	try:
		rules = json.loads(raw)
	except (TypeError, ValueError):
		return {}
	out = {}
	for rule in rules:
		if len(rule) < 4:
			continue
		_, column, operator, value = rule[:4]
		if isinstance(value, str) and value.startswith("eval:doc."):
			value = context.get(value[len("eval:doc.") :])
			if value in (None, ""):
				continue
		out[column] = (operator, value) if operator != "=" else value
	return out


def _offered(doctype: str, fieldname: str, column: str | None) -> bool:
	entries = [entry for (dt, _state), entry in step_forms.STEP_FORMS.items() if dt == doctype]
	if doctype in step_forms.CREATE_FORMS:
		entries.append(step_forms.CREATE_FORMS[doctype])
	for entry in entries:
		if column:
			spec = entry.tables.get(fieldname)
			if spec and column in spec.columns:
				return True
		elif fieldname in entry.fields:
			return True
	return False


# --- the patch --------------------------------------------------------------------------------


def _apply_patch(doc, entry, patch: dict, state: str | None = None):
	"""Apply `patch` to `doc` against the step's allowlist. Refuses by name; never deletes quietly."""
	where = state or _step_label(doc)
	meta = doc.meta
	for key, value in (patch or {}).items():
		if key in entry.tables:
			_apply_rows(doc, key, entry.tables[key], value, where)
			continue
		if key in entry.virtual:
			_apply_virtual(doc, key, value)
			continue
		if key not in entry.fields:
			label = meta.get_field(key).label if meta.get_field(key) else key
			frappe.throw(
				_("{0} cannot be changed at {1}. This step writes only: {2}.").format(
					frappe.bold(_(label)), frappe.bold(where), _labels(meta, entry)
				),
				title=_("Not part of this step"),
			)
		field = meta.get_field(key)
		_guard_field(doc, field, key, where)
		doc.set(key, _coerce(field, value))


def _apply_rows(doc, fieldname: str, spec, ops, where: str):
	"""Row operations, by row name: {update: [{name, ...}], add: [{...}], remove: [names]}.

	A row the patch does not mention is left alone -- the opposite of a document save, where a row
	missing from the payload is deleted. Adding and removing are separate verbs because they are
	separate permissions: a payout step may mark payees paid but must not add one.
	"""
	ops = ops if isinstance(ops, dict) else {}
	field = doc.meta.get_field(fieldname)
	child = frappe.get_meta(field.options)
	rows = {row.name: row for row in doc.get(fieldname) or []}
	table_label = _(field.label)

	for patch in ops.get("update") or []:
		row = rows.get(patch.get("name"))
		if not row:
			frappe.throw(
				_("A row of {0} is not on {1} any more. Reload the document and try again.").format(
					frappe.bold(table_label), frappe.bold(doc.name)
				),
				title=_("Changed since you opened it"),
			)
		if spec.rows == "own" and row.get(spec.own_field) != frappe.session.user:
			frappe.throw(
				_("That row of {0} belongs to {1}. Each member fills in their own.").format(
					frappe.bold(table_label), frappe.bold(row.get(spec.own_field) or _("somebody else"))
				),
				title=_("Not your row"),
			)
		for column, value in patch.items():
			if column == "name":
				continue
			_set_cell(doc, row, child, spec, column, value, table_label, where)

	for patch in ops.get("add") or []:
		if not spec.add:
			frappe.throw(
				_("Rows cannot be added to {0} at {1}.").format(frappe.bold(table_label), frappe.bold(where)),
				title=_("Not part of this step"),
			)
		row = doc.append(fieldname, dict(spec.new_row))
		for column, value in (patch or {}).items():
			if column in ("name", "idx"):
				continue
			_set_cell(doc, row, child, spec, column, value, table_label, where)

	remove = set(ops.get("remove") or [])
	if remove:
		if not spec.remove:
			frappe.throw(
				_("Rows cannot be removed from {0} at {1}.").format(frappe.bold(table_label), frappe.bold(where)),
				title=_("Not part of this step"),
			)
		doc.set(fieldname, [row for row in doc.get(fieldname) or [] if row.name not in remove])


def _set_cell(doc, row, child, spec, column, value, table_label, where):
	if column not in spec.columns:
		label = child.get_field(column).label if child.get_field(column) else column
		frappe.throw(
			_("{0} in {1} cannot be changed at {2}.").format(frappe.bold(_(label)), frappe.bold(table_label), frappe.bold(where)),
			title=_("Not part of this step"),
		)
	field = child.get_field(column)
	if doc.docstatus == 1 and not field.allow_on_submit:
		frappe.throw(
			_("{0} in {1} cannot change once {2} is submitted.").format(frappe.bold(_(field.label)), frappe.bold(table_label), doc.name),
			title=_("Not part of this step"),
		)
	row.set(column, _coerce(field, value))


def _apply_virtual(doc, name: str, value):
	if name == "required_by":
		if not value:
			frappe.throw(_("Required By cannot be empty."))
		date = getdate(value)
		# Every row in place, by the row objects the document already holds, so no row is
		# re-created and each keeps the supplier_quotation_item link that ties it to the award.
		for row in doc.get("items") or []:
			row.schedule_date = date
		doc.schedule_date = date


def _guard_field(doc, field, fieldname: str, where: str):
	"""What the registry audit guarantees, asked again at write time -- a registry edit that slips
	past the audit must not become a way to write a derived or protected field."""
	if not field:
		frappe.throw(_("{0} has no field {1}.").format(_(doc.doctype), fieldname))
	if field.read_only or int(field.permlevel or 0) > 0:
		frappe.throw(
			_("{0} is set by the system, not typed.").format(frappe.bold(_(field.label))),
			title=_("Not part of this step"),
		)
	if doc.docstatus == 1 and not field.allow_on_submit:
		frappe.throw(
			_("{0} cannot change once {1} is submitted.").format(frappe.bold(_(field.label)), doc.name),
			title=_("Not part of this step"),
		)


def _coerce(field, value):
	if value == "":
		value = None
	fieldtype = field.fieldtype
	if fieldtype == "Check":
		return 1 if value in (1, "1", True, "true") else 0
	if value is None:
		return None
	if fieldtype == "Int":
		return cint(value)
	if fieldtype in ("Float", "Currency", "Percent"):
		return flt(value)
	if fieldtype == "Date":
		return str(getdate(value))
	return value


# --- shared -------------------------------------------------------------------------------------


def _serves(doctype: str):
	if doctype not in step_forms.SPA_DOCTYPES:
		frappe.throw(_("{0} is not served here.").format(_(doctype)), frappe.PermissionError)


def _load(doctype: str, name: str, modified: str | None):
	"""The document, readable by this user, and still the version they were looking at."""
	_serves(doctype)
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	if modified and str(doc.modified) != str(modified):
		frappe.throw(
			_("{0} has changed since you opened it. Reload it to see what changed, then try again.").format(
				frappe.bold(doc.name)
			),
			frappe.TimestampMismatchError,
			title=_("Changed since you opened it"),
		)
	return doc


def _custodians(workflow, state) -> list[str]:
	return [row.allow_edit for row in workflow.states if row.state == state and row.allow_edit]


def _why_not_editable(doc, custodians: list[str]) -> str | None:
	"""Why this reader cannot edit this step, in words -- or None when they can."""
	if doc.docstatus == 2:
		return _("{0} is cancelled.").format(doc.name)
	if frappe.session.user != ADMINISTRATOR and custodians and not set(custodians) & set(frappe.get_roles()):
		return _("This step belongs to {0}.").format(", ".join(_(r) for r in custodians))
	if not doc.has_permission("write"):
		return _("You do not have permission to change {0}.").format(doc.name)
	if doc.docstatus == 1 and not doc.has_permission("submit"):
		return _("{0} is submitted, and changing it needs submit permission.").format(doc.name)
	return None


def _editable_step(doc, taking: str | None = None):
	"""The step form for the document's current state, if this user may use it -- else refuse."""
	workflow = get_workflow(doc.doctype)
	state = doc.get(workflow.workflow_state_field)
	entry = step_forms.STEP_FORMS.get((doc.doctype, state))
	custodians = _custodians(workflow, state)
	if not entry:
		frappe.throw(
			_("Nothing on {0} is edited at {1}{2}.").format(
				frappe.bold(doc.name),
				frappe.bold(_(state)),
				_(": {0} only moves it on").format(frappe.bold(_(taking))) if taking else "",
			),
			title=_("Not part of this step"),
		)
	why = _why_not_editable(doc, custodians)
	if why:
		frappe.throw(
			_("{0} is at {1}. {2} Taking {3} does not change anything else on it.").format(
				frappe.bold(doc.name), frappe.bold(_(state)), why, frappe.bold(_(taking))
			)
			if taking
			else why,
			title=_("Not your step"),
		)
	return entry


def _step_label(doc) -> str:
	name = get_workflow_name(doc.doctype)
	if not name:
		return doc.name
	return _(doc.get(get_workflow(doc.doctype).workflow_state_field) or "")


def _labels(meta, entry) -> str:
	labels = [_(meta.get_field(f).label) for f in entry.fields if meta.get_field(f)]
	labels += [_(meta.get_field(t).label) for t in entry.tables if meta.get_field(t)]
	return ", ".join(labels) or _("nothing")


def _field_spec(meta, fieldname: str, doc, required, titles) -> dict:
	field = meta.get_field(fieldname)
	spec = {
		"fieldname": fieldname,
		"label": _(field.label or fieldname),
		"fieldtype": field.fieldtype,
		"reqd": bool(field.reqd) or fieldname in (required or ()),
		"description": _(field.description) if field.description else None,
		"options": field.options if field.fieldtype in ("Link", "Dynamic Link") else None,
		"choices": [c for c in (field.options or "").split("\n")] if field.fieldtype == "Select" else None,
	}
	if doc is not None:
		value = doc.get(fieldname)
		spec["value"] = _plain(value)
		if field.fieldtype == "Link" and value:
			spec["display"] = titles.get(field.options, value)
		if field.fieldtype == "Currency":
			spec["currency"] = _currency(doc)
	return spec


def _virtual_spec(doc, name: str) -> dict:
	spec = dict(step_forms.VIRTUAL_FIELDS[name])
	spec["fieldname"] = name
	if name == "required_by":
		dates = sorted(str(row.schedule_date) for row in doc.get("items") or [] if row.schedule_date)
		spec["value"] = dates[0] if dates else _plain(doc.get("schedule_date"))
	return spec


def _currency(doc) -> str | None:
	return doc.get("currency") or frappe.db.get_default("currency")


# What a document is called, for the three FoLT doctypes that declare no title_field: a requisition
# is known by its activity, an evaluation by the competition, a waiver by whose purchase it is.
_TITLE_FALLBACK = {
	"Activity Requisition": "activity_program",
	"Procurement Committee Evaluation": "request_for_quotation",
	"Derogation Waiver Request": "organisation_project_name",
	"Participant Reimbursement List": "activity",
}


def _title_of(doc) -> str:
	title_field = doc.meta.title_field or _TITLE_FALLBACK.get(doc.doctype)
	value = title_field and doc.get(title_field)
	if not value:
		return doc.name
	# A fallback that is a link (a list's activity is a Project) reads as the thing it links to --
	# "Gender Budgeting Public Participation", not PROJ-0001 -- where the reader may see its title.
	field = doc.meta.get_field(title_field)
	if field and field.fieldtype == "Link":
		return _Titles().get(field.options, value) or value
	return value


def _plain(value):
	"""JSON-safe, and dates as the ISO strings an <input type=date> takes."""
	if value is None:
		return None
	if hasattr(value, "isoformat"):
		return str(value)
	if isinstance(value, (int, float, str, bool)):
		return value
	return str(value)


def _parse(value) -> dict:
	if not value:
		return {}
	if isinstance(value, str):
		value = json.loads(value)
	return value if isinstance(value, dict) else {}


class _Titles:
	"""Display labels for link values, cached per request, and only for doctypes the reader may
	list -- a title is not worth leaking a record the reader could not otherwise see exists."""

	def __init__(self):
		self.cache = {}
		self.allowed = {}

	def get(self, doctype: str | None, name):
		if not doctype or not name:
			return None
		if doctype not in self.allowed:
			self.allowed[doctype] = bool(frappe.has_permission(doctype, "select") or frappe.has_permission(doctype, "read"))
		if not self.allowed[doctype]:
			return None
		key = (doctype, name)
		if key not in self.cache:
			meta = frappe.get_meta(doctype)
			field = "full_name" if doctype == "User" else meta.title_field
			value = frappe.db.get_value(doctype, name, field) if field and meta.get_field(field) or doctype == "User" else None
			self.cache[key] = value if value and value != name else None
		return self.cache[key]
