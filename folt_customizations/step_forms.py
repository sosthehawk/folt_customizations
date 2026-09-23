"""Which fields each workflow step may write -- the registry behind /folt's step forms.

Frappe does not enforce "who may edit which field at which step" on its own. `read_only` is a
client-side property: frappe.client.set_value writes a read-only field without complaint. And a
workflow state's `allow_edit` role was only ever honoured by the Desk's form script until
workflow_access.enforce_state_custodian made it a server rule. That rule answers WHO may change a
document at a step. This table answers the other half -- WHICH FIELDS the step takes -- and
spa.py refuses, by name, anything not listed here. Without it the SPA's save endpoint would be a
general-purpose writer for every field the custodian's DocPerm allows, including the audit fields
FoLT's controllers derive and would silently overwrite.

A state that is not in STEP_FORMS is ACT-ONLY: its custodian approves or turns it down and edits
nothing here. That is the default and it covers most states. The ones that do take input were
read off the three form inventories that preceded this module, and each carries its reason.

WHAT THIS DELIBERATELY DOES NOT DO:

  - Parse `depends_on`. Several are `eval:` expressions written for the Desk (`pending_amount`'s
    reads `cur_frm`), and a renderer that evaluates them is how a generic form goes wrong. The
    handful of conditional fields declare `show_if` here instead, as plain equality.
  - Offer ERPNext transaction grids. Purchase Order items, Expense Claim receipts, Salary Slip
    components and Payment Entries stay in the Desk: they re-run taxes, pricing and ledger
    derivations on every save, and an SPA form over them re-implements ERPNext and breaks on each
    upgrade. The one PO field that touches the items -- Required By -- is a VIRTUAL field that sets
    every row's `schedule_date` in place, because the header date is overwritten from the rows.
  - Enforce anything that is already enforced. The custodian, the reason, self-approval and every
    controller rule stay where they are; audit() checks this table against the live meta instead,
    so a field renamed in a doctype fails a test rather than misleading a form.

Field labels, types, options and `reqd` are read from frappe.get_meta at request time, so what a
step form calls a field is by construction what the Desk calls it.
"""

import frappe
from frappe.model.workflow import get_workflow_name


class Table(frappe._dict):
	"""A child table a step may touch.

	`columns` are the writable ones; `show` are displayed beside them read-only. `rows="own"`
	restricts every write to rows whose `own_field` is the session user -- the committee rule,
	checked here first so a refusal names the row rather than surfacing as enforce_self_scoring's
	throw. `add` and `remove` are off unless said: omitted rows are never deleted by a patch, and
	after submit neither is ever allowed (audit() asserts it)."""


class Step(frappe._dict):
	"""The writable part of one (doctype, state). `required` adds to the meta's own `reqd`."""


def step(fields=(), required=(), tables=None, show_if=None, virtual=(), tools=()) -> Step:
	return Step(
		fields=list(fields),
		required=list(required),
		tables=tables or {},
		show_if=show_if or {},
		virtual=list(virtual),
		tools=list(tools),
	)


def table(columns=(), show=(), add=False, remove=False, rows="all", own_field="member", required=(), new_row=None) -> Table:
	return Table(
		columns=list(columns),
		show=list(show),
		add=add,
		remove=remove,
		rows=rows,
		own_field=own_field,
		required=list(required),
		new_row=new_row or {},
	)


# --- the registry -----------------------------------------------------------------------------

_REQUISITION = (
	"activity_program",
	"activity_date",
	"activity_end_date",
	"venue",
	"budget_amount",
	"budget_line",
	"cost_center",
	"donor",
	"grant_project",
	"float_required",
	"float_holder",
	"float_amount",
	"description",
	"concept_note",
)
# The float fields mean nothing on an activity with no cash component. company, requested_by and
# project are filled by the controller (set_defaults / before_submit) and are not offered.
_REQUISITION_SHOW_IF = {"float_holder": {"float_required": 1}, "float_amount": {"float_required": 1}}

_REGISTER_ROW = (
	"participant",
	"participant_name",
	"mobile_number",
	"location",
	"category",
	"attended",
	"eligible_for_reimbursement",
	"acknowledgement",
	"id_number",
	"gender",
	"is_pwd",
	"photo_consent",
	"remarks",
)

# An omitted `acknowledgement` defaults to "Signature" on both participant tables, and on a
# reimbursement row that counts as the payee's signed receipt. A row added here has been signed
# by nobody yet, so it says so -- the same "" fetch_participants and autofill_roster write.
_UNACKNOWLEDGED = {"acknowledgement": ""}

_PAYOUT_COLUMNS = ("payment_status", "acknowledgement", "payment_reference", "justification")

_FLOAT_REQUEST = (
	"purpose",
	"advance_amount",
	"posting_date",
	"folt_budget_line",
	"folt_donor_code",
	"folt_project",
	"repay_unclaimed_amount_from_salary",
)

STEP_FORMS: dict[tuple[str, str], Step] = {
	# --- Activity Requisition ---------------------------------------------------------------
	("Activity Requisition", "Draft"): step(fields=_REQUISITION, show_if=_REQUISITION_SHOW_IF),
	# The Head of Finance codes the activity against the budget before approving it; approval
	# submits, which is when before_submit opens the Project if none is named.
	("Activity Requisition", "Pending Head of Finance"): step(
		fields=("budget_line", "project", "cost_center", "donor", "grant_project"),
	),
	# --- Attendance register ------------------------------------------------------------------
	("Activity Participant List", "Draft"): step(
		fields=("activity", "session_date", "venue", "attendance_sheet"),
		tables={
			"participants": table(
				columns=_REGISTER_ROW,
				add=True,
				remove=True,
				required=("participant_name", "category"),
				new_row=_UNACKNOWLEDGED,
			)
		},
		tools=("autofill_roster", "import_sheet", "reconcile", "print_sheet"),
	),
	# The Head of Programs holds this step and may reconcile who signed before verifying -- the
	# table is not allow_on_submit, so after Verify nobody can.
	("Activity Participant List", "Pending Verification"): step(
		fields=("attendance_sheet",),
		tables={
			"participants": table(
				columns=("attended", "acknowledgement"),
				show=("participant_name", "mobile_number", "category"),
			)
		},
		tools=("reconcile",),
	),
	# The one field still open after verification: the signed sheet arrives late.
	("Activity Participant List", "Verified"): step(fields=("attendance_sheet",)),
	# --- Reimbursement list -------------------------------------------------------------------
	("Participant Reimbursement List", "Draft"): step(
		fields=("attendance_reference", "signed_list"),
		tables={
			"participants": table(
				# `justification` is always offered, not only when rate_basis says so: rate_basis
				# is computed on the server and not stored when the save throws, so the Desk hides
				# the field in exactly the case it is required.
				columns=(
					"participant_name",
					"mobile_number",
					"location",
					"id_number",
					"transport",
					"sustenance",
					"accommodation",
					"off_register",
					"justification",
				),
				show=("amount", "rate_basis", "category"),
				add=True,
				remove=True,
				required=("participant_name",),
				new_row={**_UNACKNOWLEDGED, "payment_status": "Pending"},
			)
		},
		tools=("fetch_participants",),
	),
	# Recording the payout. Only the allow_on_submit columns; the Finance Assistant holds both
	# states (the custodian/actor pairing activity_chain.py's docstring had to fix).
	("Participant Reimbursement List", "Approved"): step(
		fields=("signed_list",),
		tables={
			"participants": table(
				columns=_PAYOUT_COLUMNS,
				show=("participant_name", "mobile_number", "amount"),
			)
		},
		tools=("mark_all_paid",),
	),
	("Participant Reimbursement List", "Partly Paid"): step(
		fields=("signed_list",),
		tables={
			"participants": table(
				columns=_PAYOUT_COLUMNS,
				show=("participant_name", "mobile_number", "amount"),
			)
		},
		tools=("mark_all_paid",),
	),
	("Participant Reimbursement List", "Disputed"): step(
		fields=("signed_list",),
		tables={
			"participants": table(
				columns=_PAYOUT_COLUMNS,
				show=("participant_name", "mobile_number", "amount"),
			)
		},
	),
	# --- Committee evaluation -----------------------------------------------------------------
	# The buyer names the committee. The RFQ is fixed once the hand-off has set it: changing it
	# rebuilds every score row blank.
	("Procurement Committee Evaluation", "Draft"): step(
		fields=("quorum", "activity_requisition"),
		tables={"members": table(columns=("member",), add=True, remove=True, required=("member",))},
	),
	# Each member scores their own rows and signs their own review; the recommendation is the
	# committee's. The grid itself is rebuilt by sync_quotation_scores on every save, which is why
	# rows are addressed by name and never re-sent.
	("Procurement Committee Evaluation", "Committee Reviewing"): step(
		fields=("recommended_supplier_quotation", "recommendation_notes"),
		tables={
			"quotation_scores": table(
				columns=("score", "comments"),
				show=("member", "supplier", "supplier_quotation", "quotation_amount"),
				rows="own",
			),
			"members": table(columns=("reviewed",), show=("member",), rows="own"),
		},
	),
	# --- Derogation / waiver ------------------------------------------------------------------
	("Derogation Waiver Request", "Draft"): step(
		fields=(
			"organisation_project_name",
			"estimated_value",
			"items_service",
			"reasons",
			"risks_created",
			"mitigating_actions",
			"period_of_derogation",
			"procedure_should_have",
			"requested_procedure",
			"project",
			"cost_center",
			"supplier",
		),
	),
	# --- Purchase Order -----------------------------------------------------------------------
	# Authority and delivery date only. Supplier, items, currency and taxes are ERPNext's; the
	# order arrives from the award or waiver hand-off with all of them already in it.
	("Purchase Order", "Draft"): step(
		fields=("folt_committee_evaluation", "folt_waiver_request", "folt_supplier_group"),
		virtual=("required_by",),
	),
	# --- Float request ------------------------------------------------------------------------
	# The requester can correct it before it is checked, the Finance Officer while checking.
	# Everything after approval is the ledger's to move (float_lifecycle).
	("Employee Advance", "Requested"): step(fields=_FLOAT_REQUEST),
	("Employee Advance", "Checked"): step(fields=_FLOAT_REQUEST),
	# --- Float retirement ---------------------------------------------------------------------
	# The receipts are an ERPNext grid and stay in the Desk; the note on the claim does not.
	("Expense Claim", "Draft"): step(fields=("remark",)),
}

# Creating a document from nothing -- the two chain starters. Everything else is created by a
# hand-off from the document before it (activity_chain / procurement_chain), which is how it
# arrives already filled in and linked.
CREATE_FORMS: dict[str, Step] = {
	"Activity Requisition": step(
		fields=_REQUISITION,
		required=("activity_program", "activity_date", "budget_amount", "budget_line"),
		show_if=_REQUISITION_SHOW_IF,
	),
	"Derogation Waiver Request": step(
		fields=STEP_FORMS[("Derogation Waiver Request", "Draft")].fields,
	),
}

# Virtual fields: a value a step form edits that is not one field on the document.
VIRTUAL_FIELDS = {
	"required_by": frappe._dict(
		label="Required By",
		fieldtype="Date",
		reqd=1,
		description="Sets the delivery date on every line; the order's own date follows the earliest line.",
	),
}

# The read-only details block, per doctype: (label, fields) or (label, {"table", "columns"}).
SUMMARY: dict[str, list[tuple]] = {
	"Activity Requisition": [
		("Activity", ["activity_program", "requested_by", "activity_date", "activity_end_date", "venue"]),
		("Funding", ["company", "budget_amount", "budget_line", "project", "cost_center", "donor", "grant_project"]),
		("Cash float", ["float_required", "float_holder", "float_amount"]),
		("Concept note", ["description"]),
	],
	"Activity Participant List": [
		("Session", ["activity", "activity_title", "session_date", "venue", "activity_requisition"]),
		("Headcount", ["total_attendees", "total_eligible"]),
		(
			"Attendees",
			{"table": "participants", "columns": ["participant_name", "mobile_number", "location", "category", "attended", "acknowledgement"]},
		),
	],
	"Participant Reimbursement List": [
		("Float", ["employee_advance", "activity", "attendance_reference"]),
		("Totals", ["total_amount", "total_paid", "advance_disbursed"]),
		(
			"Payees",
			{
				"table": "participants",
				"columns": ["participant_name", "mobile_number", "location", "amount", "rate_basis", "payment_status", "acknowledgement"],
			},
		),
	],
	"Procurement Committee Evaluation": [
		("Competition", ["request_for_quotation", "activity_requisition", "requested_by", "quorum"]),
		("Recommendation", ["recommended_supplier_quotation", "recommended_supplier", "recommendation_notes"]),
		("Committee", {"table": "members", "columns": ["member", "reviewed"]}),
		(
			"Scores",
			{"table": "quotation_scores", "columns": ["member", "supplier", "supplier_quotation", "quotation_amount", "score", "comments"]},
		),
	],
	"Derogation Waiver Request": [
		("Purchase", ["organisation_project_name", "estimated_value", "supplier", "supplier_quotation", "project", "cost_center", "period_of_derogation"]),
		("Scope", ["items_service", "procedure_should_have", "requested_procedure"]),
		("Justification", ["reasons", "risks_created", "mitigating_actions"]),
	],
	"Purchase Order": [
		("Order", ["supplier", "transaction_date", "schedule_date", "folt_supplier_group", "project", "cost_center"]),
		("Authority", ["folt_committee_evaluation", "folt_waiver_request"]),
		("Totals", ["total", "total_taxes_and_charges", "grand_total"]),
		("Items", {"table": "items", "columns": ["item_code", "item_name", "qty", "uom", "rate", "amount", "schedule_date"]}),
	],
	"Employee Advance": [
		("Float", ["employee", "employee_name", "department", "posting_date", "purpose", "advance_amount"]),
		("Activity", ["folt_activity_requisition", "folt_project", "folt_budget_line", "folt_donor_code", "folt_retire_by"]),
		("Money", ["paid_amount", "claimed_amount", "return_amount", "pending_amount"]),
	],
	"Expense Claim": [
		("Claim", ["employee", "employee_name", "posting_date", "project", "cost_center", "folt_reimbursement_list", "remark"]),
		("Totals", ["total_claimed_amount", "total_sanctioned_amount", "total_advance_amount", "grand_total"]),
		("Receipts", {"table": "expenses", "columns": ["expense_date", "expense_type", "description", "amount", "sanctioned_amount"]}),
		("Against the float", {"table": "advances", "columns": ["employee_advance", "advance_paid", "unclaimed_amount", "allocated_amount"]}),
	],
	"Salary Slip": [
		("Employee", ["employee", "employee_name", "department", "designation"]),
		("Period", ["start_date", "end_date", "posting_date", "total_working_days", "payment_days", "leave_without_pay"]),
		("Pay", ["gross_pay", "total_deduction", "net_pay"]),
		("Earnings", {"table": "earnings", "columns": ["salary_component", "amount"]}),
		("Deductions", {"table": "deductions", "columns": ["salary_component", "amount"]}),
	],
	"Supplier Quotation": [
		("Bid", ["supplier", "transaction_date", "valid_till", "grand_total"]),
		("Items", {"table": "items", "columns": ["item_code", "item_name", "qty", "uom", "rate", "amount"]}),
	],
}

# Workflow doctypes the SPA serves, plus the one document the procurement chain starts from.
SPA_DOCTYPES = (
	"Activity Requisition",
	"Employee Advance",
	"Activity Participant List",
	"Participant Reimbursement List",
	"Expense Claim",
	"Procurement Committee Evaluation",
	"Derogation Waiver Request",
	"Purchase Order",
	"Salary Slip",
	"Supplier Quotation",
)

# Where a state's shape does not say what it means. Overdue is on the main path's optional branch,
# so by shape it would read as progress -- it is a float nobody has accounted for.
STATE_TONE_OVERRIDES = {"Overdue": "danger"}


# --- audit ------------------------------------------------------------------------------------

_NOT_WRITABLE_TYPES = {"Section Break", "Column Break", "Tab Break", "HTML", "Button", "Heading", "Read Only", "Table", "Table MultiSelect", "Fold"}


def audit() -> list[str]:
	"""Every way this table can be wrong about the live site. Empty list = consistent.

	Asserted by spa_e2e. Checked against the meta and the active workflows rather than against a
	copy of them, so a renamed field or state fails here instead of producing a form that saves
	nothing -- or, worse, a form the server refuses field by field.
	"""
	problems: list[str] = []

	for (doctype, state), entry in STEP_FORMS.items():
		where = f"{doctype} / {state}"
		if not get_workflow_name(doctype):
			problems.append(f"{where}: no active workflow")
			continue
		workflow = frappe.get_cached_doc("Workflow", get_workflow_name(doctype))
		row = next((s for s in workflow.states if s.state == state), None)
		if not row:
			problems.append(f"{where}: not a state of {workflow.name}")
			continue
		if not row.allow_edit:
			problems.append(f"{where}: the state names no custodian, so nobody owns its edits")
		submitted = int(row.doc_status or 0) == 1
		problems += _check_fields(doctype, entry.fields, where, submitted)
		for fieldname, spec in entry.tables.items():
			problems += _check_table(doctype, fieldname, spec, where, submitted)
		for name in entry.virtual:
			if name not in VIRTUAL_FIELDS:
				problems.append(f"{where}: unknown virtual field {name}")
		for field, condition in entry.show_if.items():
			for other in condition:
				if other not in entry.fields:
					problems.append(f"{where}: show_if on {field} reads {other}, which the step does not offer")

	for doctype, entry in CREATE_FORMS.items():
		problems += _check_fields(doctype, entry.fields, f"{doctype} / create", False)
		for fieldname in entry.required:
			if fieldname not in entry.fields:
				problems.append(f"{doctype} / create: requires {fieldname} but does not offer it")

	for doctype, sections in SUMMARY.items():
		meta = frappe.get_meta(doctype)
		for label, fields in sections:
			if isinstance(fields, dict):
				field = meta.get_field(fields["table"])
				if not field or field.fieldtype != "Table":
					problems.append(f"{doctype} summary '{label}': {fields['table']} is not a table")
					continue
				child = frappe.get_meta(field.options)
				for column in fields["columns"]:
					if not child.get_field(column):
						problems.append(f"{doctype} summary '{label}': {field.options} has no {column}")
			else:
				for fieldname in fields:
					if not meta.get_field(fieldname):
						problems.append(f"{doctype} summary '{label}': no field {fieldname}")

	return problems


def _check_fields(doctype: str, fieldnames, where: str, submitted: bool) -> list[str]:
	meta = frappe.get_meta(doctype)
	problems = []
	for fieldname in fieldnames:
		field = meta.get_field(fieldname)
		if not field:
			problems.append(f"{where}: {doctype} has no field {fieldname}")
			continue
		problems += _check_field(field, f"{where}: {fieldname}", submitted)
	return problems


def _check_field(field, where: str, submitted: bool) -> list[str]:
	problems = []
	if field.fieldtype in _NOT_WRITABLE_TYPES:
		problems.append(f"{where} is a {field.fieldtype}, which a step form cannot write")
	if field.read_only:
		problems.append(f"{where} is read_only -- the server derives it")
	if int(field.permlevel or 0) > 0:
		problems.append(f"{where} is permlevel {field.permlevel}, which is silently reverted for most roles")
	if submitted and not field.allow_on_submit:
		problems.append(f"{where} is not allow_on_submit, so it cannot change at a submitted state")
	return problems


def _check_table(doctype: str, fieldname: str, spec: Table, where: str, submitted: bool) -> list[str]:
	meta = frappe.get_meta(doctype)
	field = meta.get_field(fieldname)
	if not field or field.fieldtype != "Table":
		return [f"{where}: {fieldname} is not a child table of {doctype}"]
	problems = []
	if submitted and not field.allow_on_submit:
		problems.append(f"{where}: table {fieldname} is not allow_on_submit")
	if submitted and (spec.add or spec.remove):
		problems.append(f"{where}: rows cannot be added or removed after submit")
	child = frappe.get_meta(field.options)
	for column in spec.columns:
		child_field = child.get_field(column)
		if not child_field:
			problems.append(f"{where}: {field.options} has no field {column}")
			continue
		problems += _check_field(child_field, f"{where}: {fieldname}.{column}", submitted)
	for column in spec.show:
		if not child.get_field(column):
			problems.append(f"{where}: {field.options} has no field {column} (shown)")
	if spec.rows == "own" and not child.get_field(spec.own_field):
		problems.append(f"{where}: {fieldname} is own-rows-only but has no {spec.own_field}")
	return problems
