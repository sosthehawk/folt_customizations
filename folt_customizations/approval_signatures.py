"""Who approved a FoLT document, named on the document, and signed for.

Every FoLT approval chain already records *that* a document moved -- the workflow state and a
bare timeline comment -- but not who moved it in which capacity, and the printed forms carried
blank sign-off lines with a role written under them ("Authorised by: Executive Director") for
somebody to sign by hand afterwards. This module fills those lines in:

  - `record` stamps every workflow move as a FoLT Approval Signature row: the user, their name
    and designation, the role they acted in, when, and their signature. It hangs off the save
    the move makes (hooks.doc_events "*"), so it is the same for every way a document can be
    moved -- the Desk button, /folt, the module portals behind the React UI, a bulk approve, a
    script -- and a row exists exactly when the move stuck.

  - The signature comes from the sign dialog on the Desk's workflow buttons (folt_approvals.js,
    parked by `hold_signature` for the one action that follows it, exactly as a rejection reason
    is), or failing that from the signature the user saved as theirs (`folt_signature` on User).
    No signature is not a refusal: an approval made from a screen with no pad still names its
    approver, and the printed line is left blank to sign by hand.

  - A return or a rejection supersedes every step before it, because the document is going round
    again and the next pass has to be signed afresh. Superseded steps stay in the history.

  - Prints read the steps through the Jinja methods `folt_approvals` and `folt_signoff`
    (hooks.jinja): FoLT's own formats fill their sign-off cells, and every other print gets the
    hidden `folt_approvals_html` field, which frappe's standard print renders though the form
    does not show it.

Master-data lifecycles (masterdata.MASTERS) are recorded, because who approved a donor is worth
knowing, but they are not printed or signed: they are records, not forms anyone signs.
"""

import re

import frappe
from frappe import _
from frappe.model.workflow import get_workflow, get_workflow_name
from frappe.utils import now_datetime

from folt_customizations.workflow_access import REJECTION_REASON_FIELD, is_turn_down

DOCTYPE = "FoLT Approval Signature"
USER_SIGNATURE_FIELD = "folt_signature"
SECTION_FIELD = "folt_approvals_section"
HTML_FIELD = "folt_approvals_html"
APPROVALS_TEMPLATE = "folt_customizations/print_format_templates/_approvals.html"

# Long enough for the round trip from the dialog to the workflow button; never where state lives.
SIGNATURE_HOLD_SECONDS = 120
# A drawn signature from frappe's pad is a few kilobytes of PNG. Anything near this is not one.
MAX_SIGNATURE_LENGTH = 400_000
_DATA_URI = re.compile(r"^data:image/(png|jpeg|svg\+xml);base64,[A-Za-z0-9+/=\s]+$")


# --- recording a move ---------------------------------------------------------------------


def record(doc, method=None):
	"""on_update / on_update_after_submit / on_cancel on every doctype: stamp a workflow move."""
	if doc.doctype == DOCTYPE or not get_workflow_name(doc.doctype):
		return

	before = doc.get_doc_before_save()
	if not before:
		return

	workflow = get_workflow(doc.doctype)
	from_state = before.get(workflow.workflow_state_field)
	to_state = doc.get(workflow.workflow_state_field)
	if not from_state or not to_state or from_state == to_state:
		return

	user = frappe.session.user
	transition, role = _transition_taken(workflow, from_state, to_state, user)
	turn_down = is_turn_down(workflow, from_state, to_state)

	if turn_down:
		# Going round again: whatever was signed on the last pass no longer stands.
		frappe.db.set_value(
			DOCTYPE,
			{"reference_doctype": doc.doctype, "reference_name": doc.name, "superseded": 0},
			"superseded",
			1,
			update_modified=False,
		)

	signature = None
	if not turn_down:
		signature = held_signature(doc.doctype, doc.name, before.modified) or saved_signature(user)

	frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"reference_doctype": doc.doctype,
			"reference_name": doc.name,
			"action": transition.action if transition else _("Moved to {0}").format(_(to_state)),
			"from_state": from_state,
			"to_state": to_state,
			"turn_down": 1 if turn_down else 0,
			# The turn-down itself is history, not a signature that stands on the next pass.
			"superseded": 1 if turn_down else 0,
			"reason": ((doc.get(REJECTION_REASON_FIELD) or "").strip() or None) if turn_down else None,
			"approver": user,
			"approver_name": frappe.utils.get_fullname(user),
			"designation": _designation(user),
			"role": role,
			"signed_on": now_datetime(),
			"signature": signature,
		}
	).insert(ignore_permissions=True)

	forget_held_signature(doc.doctype, doc.name, before.modified)


def forget_document(doc, method=None):
	"""on_trash on every doctype: a deleted document's steps go with it."""
	if doc.doctype == DOCTYPE or not get_workflow_name(doc.doctype):
		return
	frappe.db.delete(DOCTYPE, {"reference_doctype": doc.doctype, "reference_name": doc.name})


def _transition_taken(workflow, from_state, to_state, user):
	"""The transition this move made, and the role on it the mover holds.

	Several rows can share a pair of states (Draft -> Pending Approval is allowed to Purchase User
	and Purchase Manager as separate rows), so the role is read from the row the user qualifies
	for. A move no transition explains -- float_lifecycle deriving a state -- has neither.
	"""
	candidates = [t for t in workflow.transitions if t.state == from_state and t.next_state == to_state]
	if not candidates:
		return None, None
	roles = set(frappe.get_roles(user))
	for t in candidates:
		if t.allowed in roles:
			return t, t.allowed
	return candidates[0], None


def _designation(user):
	return frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, "designation") or frappe.db.get_value(
		"Employee", {"user_id": user}, "designation"
	)


# --- the signature for the next move --------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def hold_signature(doctype: str, name: str, signature: str, remember: int = 0) -> None:
	"""Park the signature just drawn, for the workflow action that follows it.

	The same hand-off as workflow_access.hold_rejection_reason, for the same reason: the workflow
	button posts to `apply_workflow`, which reloads the document and drops anything else the
	browser sent. Keyed by user, document and its stored version; consumed by `record` once the
	move has stuck. `remember` also keeps it as the user's own signature for next time.
	"""
	frappe.has_permission(doctype, "read", doc=name, throw=True)
	signature = _clean(signature)
	if not signature:
		frappe.throw(_("Sign in the box before approving."), title=_("A signature is required"))

	park_signature(doctype, name, signature)
	if frappe.utils.cint(remember):
		_save_user_signature(signature)


def park_signature(doctype: str, name: str, signature: str):
	"""Hold `signature` for the next move of this document. Returns the version it is keyed to."""
	modified = frappe.db.get_value(doctype, name, "modified")
	frappe.cache.set_value(
		_signature_key(doctype, name, modified),
		signature,
		user=frappe.session.user,
		expires_in_sec=SIGNATURE_HOLD_SECONDS,
	)
	return modified


def held_signature(doctype: str, name: str, modified) -> str | None:
	return frappe.cache.get_value(_signature_key(doctype, name, modified), user=frappe.session.user)


def forget_held_signature(doctype: str, name: str, modified) -> None:
	frappe.cache.delete_value(_signature_key(doctype, name, modified), user=frappe.session.user)


def _signature_key(doctype: str, name: str, modified) -> str:
	return f"folt_signature:{doctype}:{name}:{modified}"


def _clean(signature: str | None) -> str | None:
	signature = (signature or "").strip()
	if not signature:
		return None
	if len(signature) > MAX_SIGNATURE_LENGTH or not _DATA_URI.match(signature):
		frappe.throw(_("That is not a drawn signature."), title=_("Sign again"))
	return signature


# --- the user's own signature ---------------------------------------------------------------


def saved_signature(user: str) -> str | None:
	if not frappe.get_meta("User").has_field(USER_SIGNATURE_FIELD):
		return None
	return frappe.db.get_value("User", user, USER_SIGNATURE_FIELD) or None


@frappe.whitelist()
def my_signature() -> dict:
	"""The signature the user keeps for approvals, to prefill the pad with."""
	return {"signature": saved_signature(frappe.session.user)}


@frappe.whitelist(methods=["POST"])
def save_my_signature(signature: str | None = None) -> dict:
	"""Keep (or, with nothing, clear) the user's own signature. Their own record only."""
	_save_user_signature(_clean(signature))
	return my_signature()


def _save_user_signature(signature):
	if frappe.session.user == "Guest":
		raise frappe.PermissionError
	# Straight to the column: a person keeps their own signature without needing write on User,
	# which most FoLT roles rightly do not have.
	frappe.db.set_value("User", frappe.session.user, USER_SIGNATURE_FIELD, signature, update_modified=False)


# --- reading the steps back -----------------------------------------------------------------

_FIELDS = (
	"name",
	"action",
	"from_state",
	"to_state",
	"turn_down",
	"superseded",
	"reason",
	"approver",
	"approver_name",
	"designation",
	"role",
	"signed_on",
	"signature",
)


def approvals_for(doctype: str, name: str, *, current_only: bool = False) -> list[dict]:
	filters = {"reference_doctype": doctype, "reference_name": name}
	if current_only:
		filters["superseded"] = 0
	return frappe.get_all(DOCTYPE, filters=filters, fields=list(_FIELDS), order_by="signed_on asc, creation asc")


@frappe.whitelist()
def get_approvals(doctype: str, name: str) -> dict:
	"""Every step the document has taken, oldest first, for whoever may read the document."""
	frappe.has_permission(doctype, "read", doc=name, throw=True)
	return {"steps": approvals_for(doctype, name), "signing": doctype in signing_doctypes()}


def folt_approvals(doc) -> list[dict]:
	"""Jinja: the steps that stand on the document now, oldest first. For prints."""
	if not doc or not doc.get("name") or doc.get("__islocal"):
		return []
	return approvals_for(doc.doctype, doc.name, current_only=True)


def folt_signoff(doc, actions) -> dict | None:
	"""Jinja: the latest standing step taken by one of `actions` -- the person who signs a cell.

	`actions` is a workflow action name or a list of them; a list covers a step two transitions
	can make, such as a Purchase Order approved by the Finance Manager outright or by the
	Executive Director above the threshold.
	"""
	if isinstance(actions, str):
		actions = [actions]
	steps = [s for s in folt_approvals(doc) if s.action in actions]
	return steps[-1] if steps else None


# --- which doctypes are signed, and the print field -----------------------------------------


def signing_doctypes() -> list[str]:
	"""Doctypes with an active workflow that are forms people sign: every one but master data."""
	from folt_customizations.masterdata import MASTERS

	return sorted(
		dt
		for dt in frappe.get_all("Workflow", filters={"is_active": 1}, pluck="document_type")
		if dt not in MASTERS
	)


def signing_map() -> dict:
	"""{doctype: {state_field, signs: [[state, action], ...]}} -- the moves the Desk asks a signature for.

	A signature is asked for every forward move out of a draft (docstatus 0) state: that is the
	approval phase on every FoLT chain, from the requester's submission to the final approval.
	What happens after approval -- disbursing, marking paid, settling, cancelling -- is recorded
	with its name but is bookkeeping, not something anyone signs. Turn-downs ask for a reason
	instead (folt_workflow.js), never both.
	"""
	result = {}
	for dt in signing_doctypes():
		workflow = get_workflow(dt)
		draft = {row.state for row in workflow.states if int(row.doc_status or 0) == 0}
		signs = sorted(
			{
				(t.state, t.action)
				for t in workflow.transitions
				if t.state in draft and not is_turn_down(workflow, t.state, t.next_state)
			}
		)
		if signs:
			result[dt] = {"state_field": workflow.workflow_state_field, "signs": [list(s) for s in signs]}
	return result


def add_signing_to_bootinfo(bootinfo):
	"""extend_bootinfo: which workflow moves the Desk asks a signature for. See signing_map."""
	bootinfo.folt_signing = signing_map()


def apply_approval_fields():
	"""after_migrate: the user's signature field, and the print-only approvals field per doctype.

	Derived from the active workflows rather than listed, so a chain added later gets its sign-off
	block with no second list to keep in step. The approvals field is `hidden`: frappe's standard
	print renders an HTML field's template whether or not it is hidden (printview.make_layout),
	while the form shows the steps in its own panel (folt_approvals.js).
	"""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	fields = {
		"User": [
			{
				"fieldname": USER_SIGNATURE_FIELD,
				"fieldtype": "Signature",
				"label": "Approval signature",
				"insert_after": "user_image",
				"description": "Signed onto documents you approve. You can change it whenever you sign.",
				"no_copy": 1,
				"print_hide": 1,
			}
		]
	}
	for dt in signing_doctypes():
		meta = frappe.get_meta(dt)
		ours = (SECTION_FIELD, HTML_FIELD)
		# Not after a trailing Tab Break: ERPNext ends many forms with a Connections tab, and a field
		# placed after it lands inside that tab instead of at the end of the form.
		last = [df.fieldname for df in meta.fields if df.fieldname not in ours and df.fieldtype != "Tab Break"]
		fields[dt] = [
			{
				"fieldname": SECTION_FIELD,
				"fieldtype": "Section Break",
				"label": "Approvals",
				"insert_after": last[-1] if last else None,
				"hidden": 1,
				"print_hide": 0,
			},
			{
				"fieldname": HTML_FIELD,
				"fieldtype": "HTML",
				"label": "Approvals",
				"insert_after": SECTION_FIELD,
				"hidden": 1,
				"print_hide": 0,
				"options": '{%% include "%s" %%}' % APPROVALS_TEMPLATE,
			},
		]
	create_custom_fields(fields, update=True)

