"""Approval before posting: Journal Entry, Payment Entry and Purchase Invoice.

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
from frappe.model.workflow import get_workflow, get_workflow_name

from folt_customizations.workflow_access import _is_a_person_acting

GOVERNED = ("Journal Entry", "Payment Entry", "Purchase Invoice")

# The whitelisted methods that save or submit whatever document the caller hands them.
_GENERIC_WRITES = {
	"frappe.client.submit",
	"frappe.client.cancel",
	"frappe.desk.form.linked_with.cancel_all_linked_docs",
	# The FoLT app's own document endpoints (finance_portal), which hand a person's request to
	# the document as directly as frappe.client does.
	"folt_customizations.finance_portal.save",
	"folt_customizations.finance_portal.act",
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
	if from_state != to_state and any(
		t.state == from_state and t.next_state == to_state and t.allowed in roles for t in workflow.transitions
	):
		return

	frappe.throw(
		(
			_("{0} {1} posts to the ledger only when it is approved. Submit it for approval instead.")
			if doc.docstatus == 1
			else _("{0} {1} is reversed only through its approval workflow, by whoever may cancel it there.")
		).format(_(doc.doctype), frappe.bold(doc.name or _("(new)"))),
		frappe.PermissionError,
		title=_("Needs approval"),
	)


def _addressed_directly(doctype: str) -> bool:
	"""Whether the current web request is a generic document write aimed at this doctype."""
	request = getattr(frappe.local, "request", None)
	if not request:
		return False
	path = unquote(request.path or "")
	if path.startswith(_RESOURCE_PREFIXES):
		return path.split("/")[3 if path.startswith("/api/resource/") else 4] == doctype
	cmd = frappe.form_dict.get("cmd") or (path.split("/api/method/", 1)[1] if "/api/method/" in path else "")
	if cmd in _GENERIC_WRITES:
		return True
	if cmd == "run_doc_method":
		addressed = frappe.form_dict.get("dt")
		if not addressed and frappe.form_dict.get("docs"):
			addressed = frappe.parse_json(frappe.form_dict.docs).get("doctype")
		return addressed == doctype
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
