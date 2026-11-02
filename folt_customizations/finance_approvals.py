"""Approval before posting: Journal Entry, Payment Entry and Purchase Invoice -- and, since
ADR-008, the buying documents that go forward the same way: the purchase requisition (Material
Request), the committee evaluation, the derogation waiver and the purchase order.

Each of the three carries a workflow (fixtures: FoLT Journal Entry / Payment Entry / Purchase
Invoice Approval). The preparer -- Accounts User -- submits it for approval, an Accounts Manager
who did not prepare it approves, and approving is what submits the voucher to the ledger. Reject
sends it back to Draft, which workflow_access.is_turn_down already reads as a turn-down, so the
reason is required and lands in the timeline with no code here.

What this module adds is the half Frappe leaves open. Frappe checks a workflow transition only
when the workflow state itself changes, and it stamps the submitted state on a document AFTER
that check (document.py validate_workflow -> set_workflow_state_on_action). So anybody holding
Submit on the doctype -- every Accounts User -- could post a draft voucher straight to the ledger
through the REST API, `frappe.client.submit` or the list view's bulk action, and the approval
would never have happened. The Desk only hides the button.

The guard refuses exactly those doors: a generic document endpoint that a person calls to submit
THIS document, when the move is not one of the workflow's transitions. It deliberately lets
through the documents ERPNext and HRMS generate as the consequence of something already
authorised -- the payroll accrual a posted payroll run makes, scheduled depreciation, exchange
gain/loss, the bank reconciliation tool's matching voucher -- because those are inserted and
submitted by server code inside some other request, not addressed by one. Data Import is a
system flag (workflow_access.SYSTEM_FLAGS), so opening balances can still be loaded in bulk by
whoever is allowed to import.
"""

from urllib.parse import unquote

import frappe
from frappe import _
from frappe.model.workflow import get_workflow, get_workflow_name, has_approval_access, is_transition_condition_satisfied

from folt_customizations.workflow_access import _is_a_person_acting

# The buying documents joined in ADR-008 (phase 8): the requisition, the committee's evaluation,
# the waiver and the order each go forward only by their approval workflow, and each was open to a
# direct submit by a role that holds Submit on it (Purchase and Stock users on the requisition,
# Purchase users on the order, the Head of Finance on the evaluation, Finance Officer and Executive
# Director on the waiver).
GOVERNED = (
	"Journal Entry", "Payment Entry", "Purchase Invoice",
	"Material Request", "Purchase Order", "Procurement Committee Evaluation", "Derogation Waiver Request",
)
# Governed documents that are not ledger vouchers: refused in the same words, minus the ledger.
_NOT_LEDGER = {"Material Request", "Purchase Order", "Procurement Committee Evaluation", "Derogation Waiver Request"}

# The whitelisted methods that save or submit whatever document the caller hands them.
_GENERIC_WRITES = {
	"frappe.client.submit",
	"frappe.client.cancel",
	"frappe.desk.form.linked_with.cancel_all_linked_docs",
	# The FoLT app's own document endpoints (finance_portal, procurement_portal), which hand a person's request to
	# the document as directly as frappe.client does.
	"folt_customizations.finance_portal.save",
	"folt_customizations.finance_portal.act",
	"folt_customizations.procurement_portal.save",
	"folt_customizations.procurement_portal.act",
	"frappe.client.save",
	"frappe.client.insert",
	"frappe.client.insert_many",
	"frappe.client.set_value",
	"frappe.client.bulk_update",
	"frappe.desk.form.save.savedocs",
}
_RESOURCE_PREFIXES = ("/api/resource/", "/api/v1/resource/", "/api/v2/document/")


def refuse_direct_submit(doc, method=None):
	"""before_submit and before_cancel on the governed doctypes: post or reverse a voucher only
	by an approval step, never around one.

	The move has to be one of the workflow's transitions AND one this person may make. The second
	half matters for cancel: Frappe stamps Cancelled on a directly cancelled document itself, and
	Approved -> Cancelled is a real transition -- just not one an Accounts User holds.
	"""
	if doc.doctype not in GOVERNED or not get_workflow_name(doc.doctype):
		return
	if not _is_a_person_acting(doc) or not _addressed_directly(doc.doctype):
		return

	workflow = get_workflow(doc.doctype)
	field = workflow.workflow_state_field
	before = doc.get_doc_before_save()
	from_state = (before.get(field) if before else None) or workflow.states[0].state
	to_state = doc.get(field)
	roles = set(frappe.get_roles())
	# The move must be a transition this person may make as apply_workflow would allow it: their
	# role, the transition's condition (the order's approval tier) and its self-approval rule.
	# Matching on role alone let a Finance Manager post an order the Executive Director must
	# approve, and an approver approve their own document, through the REST API.
	user = frappe.session.user
	if from_state != to_state and any(
		t.state == from_state
		and t.next_state == to_state
		and t.allowed in roles
		and has_approval_access(user, before or doc, t)
		and is_transition_condition_satisfied(t, doc)
		for t in workflow.transitions
	):
		return

	frappe.throw(
		(
			_("{0} {1} goes forward only when it is approved. Submit it for approval instead.")
			if doc.docstatus == 1 and doc.doctype in _NOT_LEDGER
			else _("{0} {1} posts to the ledger only when it is approved. Submit it for approval instead.")
			if doc.docstatus == 1
			else _("{0} {1} is reversed only through its approval workflow, by whoever may cancel it there.")
		).format(_(doc.doctype), frappe.bold(doc.name or _("(new)"))),
		frappe.PermissionError,
		title=_("Needs approval"),
	)


_RUN_DOC_METHOD = {"run_doc_method", "frappe.handler.run_doc_method"}
_METHOD_PREFIXES = ("/api/method/", "/api/v1/method/", "/api/v2/method/")


def _commands(path: str) -> set[str]:
	"""Every name the request could be calling a method by. The path and a `cmd` form field are both
	read, and both count: Frappe's v1 router sets `cmd` from the path, its v2 router never sets it,
	a legacy request may send `cmd` on any path -- and a caller chooses what the form carries, so
	neither one alone is to be trusted to name the method that actually runs."""
	out = set()
	for prefix in _METHOD_PREFIXES:
		if path.startswith(prefix):
			rest = path[len(prefix):]
			out.add(rest)
			# v2's /api/v2/method/<doctype>/<method>: a controller method of that doctype.
			out.update(part for part in rest.split("/") if part)
	cmd = frappe.form_dict.get("cmd")
	if cmd:
		out.add(cmd)
	return out


def _addressed_doctype() -> str | None:
	"""The doctype a run_doc_method call names, in any of the shapes v1 and v2 accept."""
	form = frappe.form_dict
	if form.get("dt"):
		return form.get("dt")
	for key in ("docs", "document", "doc"):
		if form.get(key):
			value = frappe.parse_json(form.get(key))
			if isinstance(value, dict) and value.get("doctype"):
				return value.get("doctype")
	return None


def _addressed_directly(doctype: str) -> bool:
	"""Whether the current web request is a generic document write aimed at this doctype.

	ADR-008 phase 8 closed four ways round the earlier check, which read only `cmd` and only v1's
	path: Frappe's v2 method route (/api/v2/method/frappe.client.submit) sets no `cmd`; v2's
	run_doc_method sends the document as `document`; run_doc_method is reachable by its dotted name
	(frappe.handler.run_doc_method), which handler.py exempts from the whitelist check as well; and
	Frappe's MCP endpoint (/api/v2/mcp) submits documents through tools of its own.
	"""
	request = getattr(frappe.local, "request", None)
	if not request:
		return False
	path = unquote(request.path or "")
	if path.startswith(_RESOURCE_PREFIXES):
		return path.split("/")[3 if path.startswith("/api/resource/") else 4] == doctype
	if path.startswith("/api/v2/mcp"):
		return True
	commands = _commands(path)
	if commands & _GENERIC_WRITES:
		return True
	if commands & _RUN_DOC_METHOD:
		return _addressed_doctype() in (doctype, None)
	# /api/v2/method/<doctype>/<method>: a whitelisted method on this doctype's controller.
	if path.startswith("/api/v2/method/") and doctype in commands:
		return True
	return False


@frappe.whitelist()
def submit_cancel_or_update_docs(doctype, docnames, action="submit", data=None, task_id=None):
	"""The list view's bulk action, minus bulk submit or cancel of an approval-governed voucher.

	Twenty or more documents are submitted in a background job, where there is no request for
	refuse_direct_submit to look at, so the refusal has to happen here, at the door.
	"""
	from frappe.desk.doctype.bulk_update.bulk_update import submit_cancel_or_update_docs as bulk

	if action in ("submit", "cancel") and doctype in GOVERNED and get_workflow_name(doctype):
		frappe.throw(
			_("{0} is posted and reversed only through its approval workflow, one document at a time.").format(_(doctype)),
			frappe.PermissionError,
			title=_("Needs approval"),
		)
	return bulk(doctype, docnames, action=action, data=data, task_id=task_id)
