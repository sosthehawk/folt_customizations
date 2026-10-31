"""Programs master data: the lifecycle every master record shares (folt-workflow-ui ADR-006).

A master record -- a programme, a category, a location, a donor -- is reference data other
documents link to, so it is governed rather than typed in freely:

*One lifecycle.* Every master doctype carries the same workflow, generated here from
FoLT Master Data Settings (apply_master_workflows), never shipped as a fixture, so an approver
role chosen in Settings survives the next migrate:

    Draft --Submit for Review--> Pending Review --Approve--> Active <--Reactivate/Deactivate--> Inactive
      ^                              |                                                      |   ^
      +----Return for Revision-------+--Reject--> Rejected (final)          Archive --> Archived --Restore

Return for Revision and Reject are turn-downs in workflow_access's sense (back to the first state;
into a state with no way out), so both need a reason, recorded in the timeline. Approve and Reject
refuse self-approval. A doctype whose Settings rule does not require approval gets Activate
(Draft -> Active) for its preparers instead of the review.

*Active is the state, is_active follows it.* Each state's update_field keeps `is_active` in step,
and link pickers offer active records only. Records FoLT's own code creates (seeders, the
participant setup) are reference data and start Active; records people create start in Draft.

*Nothing referenced is deleted.* A record that has ever been Active is deactivated, not deleted;
Frappe's own link check refuses the rest when something points at them.

*Codes and duplicates.* A code left blank is generated from the Settings prefix, codes are
unique per doctype, and a name that differs from an existing one only by case, punctuation or a
legal suffix ("Ltd", "Foundation") is a duplicate -- refused or, if Settings say so, warned.
"""

import re

import frappe
from frappe import _
from frappe.model.naming import make_autoname

DRAFT, PENDING, ACTIVE, INACTIVE, REJECTED, ARCHIVED = "Draft", "Pending Review", "Active", "Inactive", "Rejected", "Archived"
STATES = (DRAFT, PENDING, ACTIVE, INACTIVE, REJECTED, ARCHIVED)
STATE_STYLES = {DRAFT: "", PENDING: "Warning", ACTIVE: "Success", INACTIVE: "Inverse", REJECTED: "Danger", ARCHIVED: "Inverse"}
ACTIONS = ("Submit for Review", "Approve", "Return for Revision", "Reject", "Activate", "Deactivate", "Reactivate", "Archive", "Restore")

# Who prepares master records, who approves them, and who looks after them once approved.
# System Manager is added to each. The Settings rule can name a different approver per doctype.
PREPARERS = ("Master Data Administrator", "Program Manager", "Program Officer", "M&E Officer", "Head of Programs")
APPROVERS = ("Master Data Approver", "Head of Programs")
STEWARDS = ("Master Data Administrator", "Head of Programs")
ROLES = ("Master Data Administrator", "Master Data Approver", "Program Manager", "Program Officer", "M&E Officer")

# doctype -> (code field, default code prefix, title field, dedupe the title across the doctype)
MASTERS = {
	"FoLT Program": ("program_code", "PRG-", "program_name", True),
	"FoLT Program Category": ("category_code", "CAT-", "category_name", True),
	"FoLT Program Type": ("type_code", "TYP-", "type_name", True),
	"FoLT Program Area": ("area_code", "ARE-", "area_name", True),
	"FoLT Program Status": ("status_code", "PST-", "status_name", True),
	"FoLT Location": ("location_code", "LOC-", "location_name", False),
	"FoLT Donor": ("donor_code", "DON-", "donor_name", True),
	"FoLT Grant Project": ("grant_code", "FND-", "grant_name", True),
	"FoLT Partner": ("partner_code", "PTN-", "partner_name", True),
	"FoLT Beneficiary Category": ("category_code", "BEN-", "category_name", True),
	"FoLT Objective": ("objective_code", "OBJ-", "objective_name", True),
	"FoLT Outcome": ("outcome_code", "OUT-", "outcome_name", False),
	"FoLT Output": ("output_code", "OPT-", "output_name", False),
	"FoLT Indicator": ("indicator_code", "IND-", "indicator_name", False),
	"FoLT Activity Template": ("activity_code", "ACT-", "activity_name", True),
	"FoLT Budget Line": ("budget_line_code", "BGL-", "budget_line_name", False),
	"FoLT Funding Allocation": ("allocation_code", "ALC-", "allocation_title", False),
	"FoLT Reporting Period": ("period_code", "RPT-", "period_name", False),
}

_LEGAL_SUFFIXES = r"\b(ltd|limited|inc|incorporated|plc|llc|foundation|trust|org|organisation|organization|the)\b"


def workflow_name(doctype: str) -> str:
	return f"{doctype} Lifecycle"


def normalise(title: str | None) -> str:
	"""A name with case, punctuation and legal suffixes taken out, for duplicate detection."""
	text = re.sub(r"[^\w\s]", " ", (title or "").casefold())
	text = re.sub(_LEGAL_SUFFIXES, " ", text)
	return re.sub(r"\s+", " ", text).strip()


def _settings():
	return frappe.get_cached_doc("FoLT Master Data Settings")


def rule_for(doctype: str):
	"""The Settings row for a doctype, or None before Settings are first applied."""
	if not frappe.db.exists("DocType", "FoLT Master Data Settings"):
		return None
	return next((r for r in _settings().rules if r.master_doctype == doctype), None)


def prefix_for(doctype: str) -> str:
	rule = rule_for(doctype)
	return (rule.code_prefix if rule and rule.code_prefix else MASTERS[doctype][1]).strip()


class MasterRecord:
	"""Mixed into every master doctype's controller, ahead of Document or NestedSet."""

	def before_insert(self):
		code_field = MASTERS[self.doctype][0]
		if not (self.get(code_field) or "").strip():
			prefix = prefix_for(self.doctype)
			self.set(code_field, make_autoname(f"{prefix}.####" if not prefix.endswith(".") else f"{prefix}####", doc=self))
		parent = super()
		if hasattr(parent, "before_insert"):
			parent.before_insert()

	def validate(self):
		self._check_code()
		self._check_duplicate()
		check_master_links(self)
		state = self.get("workflow_state")
		if state:
			self.is_active = 1 if state == ACTIVE else 0
		parent = super()
		if hasattr(parent, "validate"):
			parent.validate()

	def after_insert(self):
		# FoLT's own code (seeders, participant setup) creates reference data: it starts Active.
		if self.flags.ignore_permissions and self.get("workflow_state") in (None, "", DRAFT) and not self.flags.keep_draft:
			self.db_set({"workflow_state": ACTIVE, "is_active": 1}, update_modified=False)
		parent = super()
		if hasattr(parent, "after_insert"):
			parent.after_insert()

	def on_trash(self, *args, **kwargs):
		if self.get("workflow_state") not in (None, "", DRAFT, REJECTED) and not frappe.flags.in_test_teardown:
			frappe.throw(
				_("{0} {1} has been in use. Deactivate it instead of deleting it, so the records that point at it keep their meaning.").format(
					_(self.doctype), frappe.bold(self.name)
				),
				title=_("Deactivate instead"),
			)
		parent = super()
		if hasattr(parent, "on_trash"):
			parent.on_trash(*args, **kwargs)

	def _check_code(self):
		code_field = MASTERS[self.doctype][0]
		code = (self.get(code_field) or "").strip()
		if not code:
			return
		self.set(code_field, code)
		clash = frappe.db.get_value(self.doctype, {code_field: code, "name": ["!=", self.name or ""]}, "name")
		if clash:
			frappe.throw(_("The code {0} is already used by {1}.").format(frappe.bold(code), frappe.bold(clash)), title=_("Duplicate code"))

	def _check_duplicate(self):
		_code, _prefix, title_field, dedupe = MASTERS[self.doctype]
		if not dedupe:
			return
		mine = normalise(self.get(title_field))
		if not mine:
			return
		for other, title in frappe.get_all(self.doctype, filters={"name": ["!=", self.name or ""]}, fields=["name", title_field], as_list=True):
			if normalise(title) == mine:
				message = _("{0} looks like the existing {1} {2}.").format(frappe.bold(self.get(title_field)), _(self.doctype), frappe.bold(other))
				if frappe.db.exists("DocType", "FoLT Master Data Settings") and _settings().duplicate_policy == "Warn":
					frappe.msgprint(message, title=_("Possible duplicate"), indicator="orange")
					return
				frappe.throw(message, title=_("Possible duplicate"))


def _master_links(doc):
	"""(label, target doctype, value) for every master record this document links to."""
	for df in doc.meta.get_link_fields():
		if df.options in MASTERS and df.fieldname != "old_parent" and doc.get(df.fieldname):
			yield df.label, df.options, doc.get(df.fieldname)
	for df in doc.meta.get_table_fields():
		child = frappe.get_meta(df.options)
		for cdf in child.get_link_fields():
			if cdf.options in MASTERS:
				for row in doc.get(df.fieldname) or []:
					if row.get(cdf.fieldname):
						yield df.label, cdf.options, row.get(cdf.fieldname)


def check_master_links(doc, method=None):
	"""A master record newly linked here must be Active. Links that were already there stay."""
	before = doc.get_doc_before_save() if not doc.is_new() else None
	had = {(target, value) for _label, target, value in _master_links(before)} if before else set()
	for label, target, value in _master_links(doc):
		if (target, value) in had or (target == doc.doctype and value == doc.name):
			continue
		if not frappe.db.get_value(target, value, "is_active"):
			frappe.throw(
				_("{0}: {1} is not active, so it cannot be chosen. It may still be awaiting approval, or have been deactivated.").format(
					_(label), frappe.bold(value)
				),
				title=_("Not an active record"),
			)


# --- setup: roles, states, workflows (after_install / after_migrate) --------------------------


def ensure_roles():
	for role in ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)


def ensure_states_and_actions():
	for state in STATES:
		if not frappe.db.exists("Workflow State", state):
			frappe.get_doc({"doctype": "Workflow State", "workflow_state_name": state, "style": STATE_STYLES[state]}).insert(ignore_permissions=True)
	for action in ACTIONS:
		if not frappe.db.exists("Workflow Action Master", action):
			frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": action}).insert(ignore_permissions=True)


def _with_admin(roles) -> list[str]:
	return list(dict.fromkeys([*roles, "System Manager"]))


def workflow_definition(doctype: str) -> dict:
	"""The lifecycle workflow for one master doctype, from its Settings rule."""
	rule = rule_for(doctype)
	requires_approval = bool(rule.requires_approval) if rule else True
	approvers = _with_admin([rule.approver_role] if rule and rule.approver_role else APPROVERS)
	preparers = _with_admin(PREPARERS)
	stewards = _with_admin(STEWARDS)

	def states(state, roles, active):
		return [{"state": state, "doc_status": "0", "allow_edit": r, "update_field": "is_active", "update_value": "1" if active else "0"} for r in roles]

	def moves(state, action, next_state, roles, self_approval=1):
		return [{"state": state, "action": action, "next_state": next_state, "allowed": r, "allow_self_approval": self_approval} for r in roles]

	transitions = []
	if requires_approval:
		transitions += moves(DRAFT, "Submit for Review", PENDING, preparers)
		transitions += moves(PENDING, "Approve", ACTIVE, approvers, 0)
		transitions += moves(PENDING, "Return for Revision", DRAFT, approvers, 0)
		transitions += moves(PENDING, "Reject", REJECTED, approvers, 0)
	else:
		transitions += moves(DRAFT, "Activate", ACTIVE, preparers)
	transitions += moves(ACTIVE, "Deactivate", INACTIVE, stewards)
	transitions += moves(INACTIVE, "Reactivate", ACTIVE, stewards)
	transitions += moves(INACTIVE, "Archive", ARCHIVED, stewards)
	transitions += moves(ARCHIVED, "Restore", INACTIVE, stewards)

	return {
		"doctype": "Workflow",
		"workflow_name": workflow_name(doctype),
		"document_type": doctype,
		"workflow_state_field": "workflow_state",
		"is_active": 1,
		"send_email_alert": 1,
		"override_status": 1,
		"states": (
			states(DRAFT, preparers, False)
			+ (states(PENDING, approvers, False) if requires_approval else [])
			+ states(ACTIVE, stewards, True)
			+ states(INACTIVE, stewards, False)
			+ (states(REJECTED, stewards, False) if requires_approval else [])
			+ states(ARCHIVED, stewards, False)
		),
		"transitions": transitions,
	}


def apply_master_workflows():
	"""Create or rewrite each master doctype's lifecycle workflow. Idempotent."""
	ensure_states_and_actions()
	for doctype in MASTERS:
		if not frappe.db.exists("DocType", doctype):
			continue
		definition = workflow_definition(doctype)
		name = definition["workflow_name"]
		# Before the first save: a new Workflow stamps every row with no state as its first state
		# (Draft), which would take records already in use out of every picker.
		_adopt_existing_rows(doctype)
		wf = frappe.get_doc("Workflow", name) if frappe.db.exists("Workflow", name) else frappe.new_doc("Workflow")
		wf.update({k: v for k, v in definition.items() if k not in ("states", "transitions")})
		wf.set("states", definition["states"])
		wf.set("transitions", definition["transitions"])
		wf.flags.ignore_permissions = True
		wf.save()


def _adopt_existing_rows(doctype: str):
	"""Rows made before the lifecycle existed are in use: Active, or Inactive where switched off."""
	frappe.db.sql(
		f"""update `tab{doctype}` set workflow_state = if(ifnull(is_active, 1) = 1, %s, %s)
		    where ifnull(workflow_state, '') = ''""",
		(ACTIVE, INACTIVE),
	)
	frappe.db.sql(f"update `tab{doctype}` set is_active = if(workflow_state = %s, 1, 0)", (ACTIVE,))
	code_field = MASTERS[doctype][0]
	prefix = prefix_for(doctype)
	for name in frappe.get_all(doctype, filters={code_field: ["is", "not set"]}, pluck="name", order_by="creation asc"):
		frappe.db.set_value(doctype, name, code_field, make_autoname(f"{prefix}.####"), update_modified=False)


def ensure_settings():
	"""One Settings rule per master doctype, defaults filled in, without overwriting choices made."""
	settings = frappe.get_doc("FoLT Master Data Settings")
	have = {r.master_doctype for r in settings.rules}
	changed = False
	for doctype, (_code, prefix, _title, _dedupe) in MASTERS.items():
		if doctype not in have and frappe.db.exists("DocType", doctype):
			settings.append("rules", {"master_doctype": doctype, "code_prefix": prefix, "requires_approval": 1})
			changed = True
	if not settings.duplicate_policy:
		settings.duplicate_policy = "Block"
		changed = True
	if not settings.reminder_days:
		settings.reminder_days = 7
		changed = True
	if changed:
		settings.flags.ignore_permissions = True
		settings.flags.skip_workflow_rebuild = True
		settings.save()


def apply_master_data():
	"""after_install / after_migrate: roles, Settings defaults, workflows, the location tree."""
	from folt_customizations import geography

	ensure_roles()
	ensure_settings()
	apply_master_workflows()
	geography.apply_location_tree()
	frappe.db.commit()


# --- reporting periods (daily) ----------------------------------------------------------------


def flag_reporting_periods():
	"""Daily: an open report past its due date is Overdue; one due within the Settings' reminder
	window is a Notification Log for its responsible person and the Settings' notify role."""
	from frappe.utils import add_days, getdate, nowdate

	today = getdate(nowdate())
	for name in frappe.get_all(
		"FoLT Reporting Period", filters={"reporting_status": "Open", "deadline": ["<", today], "is_active": 1}, pluck="name"
	):
		frappe.db.set_value("FoLT Reporting Period", name, "reporting_status", "Overdue")

	settings = _settings()
	days = settings.reminder_days or 0
	if days <= 0:
		return
	due = add_days(today, days)
	extra = []
	if settings.notify_role:
		extra = frappe.get_all("Has Role", filters={"role": settings.notify_role, "parenttype": "User"}, pluck="parent")
	for p in frappe.get_all(
		"FoLT Reporting Period",
		filters={"reporting_status": "Open", "deadline": due, "is_active": 1},
		fields=["name", "period_name", "deadline", "responsible", "program"],
	):
		owner = frappe.db.get_value("Employee", p.responsible, "user_id") if p.responsible else None
		for user in {u for u in [owner, *extra] if u and frappe.db.get_value("User", u, "enabled")}:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"for_user": user,
					"type": "Alert",
					"document_type": "FoLT Reporting Period",
					"document_name": p.name,
					"subject": _("Report due in {0} days: {1}").format(days, p.period_name),
					"email_content": _("{0}{1} is due on {2}.").format(
						p.period_name, f" ({p.program})" if p.program else "", frappe.utils.formatdate(p.deadline)
					),
				}
			).insert(ignore_permissions=True)
	frappe.db.commit()
