import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from folt_customizations.float_lifecycle import FUNDED_FLOAT_STATES
from folt_customizations.participants import (
	ACKNOWLEDGED_FORMS as ACKNOWLEDGED,
	RATE_COMPONENTS,
	check_duplicates,
	get_rate_schedule,
	get_rates_for_location,
	normalise_mobile,
)

UNRESOLVED = (None, "", "Pending")

# The state the list reaches once the payout is complete. Step 3 of the finance workflow --
# collect the participants' acknowledgement, then pay -- is enforced by what has to be true to
# get here, not by a state of its own: an approved list goes back to the programme officer, and
# the evidence they bring back is what lets the Finance Assistant close it off.
PAID = "Paid"


class ParticipantReimbursementList(Document):
	"""Who was paid from a float — derived from the attendance register, never typed.

	Specified as W-04B in Module 1 Annex A. The register (Activity Participant List) is
	the source; this document is scoped by the same Project, and every payee must trace
	back to a verified attendee on that project or carry a recorded justification.
	"""

	def validate(self):
		self.set_activity_from_advance()
		self.validate_register_project()
		self.validate_register_not_already_used()
		self.normalise_rows()
		check_duplicates(self.participants or [], _("reimbursement list"))
		self.validate_rows_against_register()
		self.apply_rate_basis()
		self.set_totals()
		self.validate_against_advance()
		self.warn_about_sibling_drafts()

	def warn_about_sibling_drafts(self):
		"""Say that another draft is open on the same register, without blocking this save."""
		drafts = [row for row in self._other_lists_on_register() if row.docstatus == 0]
		if not drafts:
			return

		frappe.msgprint(
			_(
				"Another draft list is open on register {0}: {1}. Only one of them can be approved — "
				"finish this one and delete the other."
			).format(
				frappe.bold(self.attendance_reference),
				", ".join(
					frappe.utils.get_link_to_form("Participant Reimbursement List", row.name)
					for row in drafts
				),
			),
			title=_("Another draft on this register"),
			indicator="orange",
		)

	def before_update_after_submit(self):
		"""Payouts are recorded against an approved list, so the paid roll-up and the
		acknowledgement rule have to hold here too — validate() does not run after submit.
		"""
		self.normalise_rows()
		self.set_totals()
		self.validate_payout_evidence()

	def validate_payout_evidence(self):
		"""A list is only fully paid once every payee has been accounted for, one way or another.

		The paper process closed this gap with judgement: some participants signed, some gave a
		thumbprint, a few cells were left blank, and the list was filed anyway. Marking the list
		Paid is the assertion that the money reached the people on it, so it needs each row
		resolved — paid and acknowledged, or explicitly not paid — and the acknowledged sheet
		itself on the document, since that sheet is what an auditor asks for.
		"""
		if self.workflow_state != PAID:
			return

		unresolved = [row for row in self.participants or [] if row.payment_status in UNRESOLVED]
		if unresolved:
			frappe.throw(
				_(
					"{0} of {1} payees are still pending: {2}. Record what happened to each one "
					"— paid, failed, or not paid — before marking the list paid."
				).format(
					len(unresolved),
					len(self.participants),
					", ".join(frappe.bold(row.participant_name) for row in unresolved[:5])
					+ (_(" and others") if len(unresolved) > 5 else ""),
				),
				title=_("Payees still pending"),
			)

		if not self.signed_list:
			frappe.throw(
				_(
					"Attach the signed reimbursement list before marking it paid. It is the "
					"participants' own acknowledgement of receipt, and the retirement of the "
					"float rests on it."
				),
				title=_("Signed list required"),
			)

	def before_submit(self):
		if not self.participants:
			frappe.throw(_("A reimbursement list cannot be approved with no participants."))

		# The other half of validate_register_not_already_used. By now this list is about to
		# become a payment instruction, so a sibling in any state at all is one too many.
		others = self._other_lists_on_register()
		if others:
			frappe.throw(
				_(
					"Register {0} is also on {1}. Two lists off one register pay every attendee twice — "
					"delete or cancel the other one, or move its payees onto this list, before approving."
				).format(
					frappe.bold(self.attendance_reference),
					", ".join(
						frappe.utils.get_link_to_form("Participant Reimbursement List", row.name)
						for row in others
					),
				),
				title=_("Two lists on one register"),
			)

		if not self.attendance_reference and not all(row.off_register for row in self.participants):
			frappe.throw(
				_(
					"Select the verified attendance register this list is derived from. "
					"A reimbursement list is derived from a register, not prepared independently of it."
				),
				title=_("Register required"),
			)

	def set_activity_from_advance(self):
		"""The project is inherited from the float, not chosen again (annex 6.4.1)."""
		if not self.employee_advance:
			return

		advance_project = frappe.db.get_value("Employee Advance", self.employee_advance, "folt_project")

		if not advance_project:
			return

		if not self.activity:
			self.activity = advance_project
		elif self.activity != advance_project:
			frappe.throw(
				_("This list is on project {0} but float {1} was approved for project {2}.").format(
					frappe.bold(self.activity), self.employee_advance, frappe.bold(advance_project)
				),
				title=_("Project mismatch"),
			)

	def validate_register_project(self):
		"""A register from another project is not usable here (F-04-D5)."""
		if not self.attendance_reference:
			return

		register = frappe.db.get_value(
			"Activity Participant List",
			self.attendance_reference,
			["activity", "docstatus"],
			as_dict=True,
		)

		if register.activity != self.activity:
			frappe.throw(
				_("Register {0} belongs to project {1}, not to {2}.").format(
					frappe.bold(self.attendance_reference), register.activity, frappe.bold(self.activity)
				),
				title=_("Register is for a different project"),
			)

		if register.docstatus != 1:
			frappe.throw(
				_("Register {0} is not verified yet. Verify it before deriving payments from it.").format(
					frappe.bold(self.attendance_reference)
				),
				title=_("Register not verified"),
			)

	def _other_lists_on_register(self) -> list[frappe._dict]:
		if not self.attendance_reference:
			return []

		return frappe.get_all(
			"Participant Reimbursement List",
			filters={
				"attendance_reference": self.attendance_reference,
				"docstatus": ["<", 2],
				"name": ["!=", self.name],
			},
			fields=["name", "docstatus"],
		)

	def validate_register_not_already_used(self):
		"""One register, one list -- refused at submit, warned about in draft.

		`activity_chain.make_reimbursement_list` already refuses a second list off one register,
		but that only covers the hand-off: a list raised by hand, or given a register after it was
		created, reached neither check, and two lists off one register pays every attendee twice.

		The split between warning and refusing is not fussiness, it is what stops the check
		deadlocking. Refusing on *save* whenever any other list exists means two drafts on one
		register can never be saved again -- each sees the other, so neither can be edited or
		corrected, and the only way out is deleting one. FoLT already had exactly that pair. A
		second draft is a mistake in progress; a second *submitted* list is a second payment
		instruction. So a submitted sibling is refused here and now, and a draft sibling is said
		out loud and refused at `before_submit`, by which time one of them has been abandoned.
		"""
		submitted = [row for row in self._other_lists_on_register() if row.docstatus == 1]
		if submitted:
			frappe.throw(
				_(
					"Register {0} has already been paid out on {1}. Everyone on a register is paid once; "
					"add any missed attendee to that list rather than starting a second one."
				).format(
					frappe.bold(self.attendance_reference),
					", ".join(
						frappe.utils.get_link_to_form("Participant Reimbursement List", row.name)
						for row in submitted
					),
				),
				title=_("Register already has a reimbursement list"),
			)

	def normalise_rows(self):
		for row in self.participants or []:
			row.mobile_number = normalise_mobile(row.mobile_number, label=row.participant_name)
			row.amount = sum(flt(row.get(component)) for component in RATE_COMPONENTS)
			row.signed = 1 if row.acknowledgement in ACKNOWLEDGED else 0

			# A payee cannot be recorded as paid without acknowledging receipt (F-04-E4).
			if row.payment_status == "Paid" and row.acknowledgement not in ACKNOWLEDGED:
				frappe.throw(
					_("Row {0}: {1} is marked paid but has no acknowledgement of receipt.").format(
						row.idx, frappe.bold(row.participant_name)
					),
					title=_("Acknowledgement required"),
				)

	def validate_rows_against_register(self):
		"""Every payee traces back to a verified attendee on this project (F-04-D4)."""
		verified_attendees = self.get_verified_attendees()

		for row in self.participants or []:
			if row.off_register:
				if not (row.justification or "").strip():
					frappe.throw(
						_("Row {0}: {1} is not on a verified register, so a justification is required.").format(
							row.idx, frappe.bold(row.participant_name)
						),
						title=_("Justification required"),
					)
				continue

			if not row.participant or row.participant not in verified_attendees:
				frappe.throw(
					_(
						"Row {0}: {1} is not on a verified attendance register for project {2}. "
						"Either add them to the register, or tick <b>Not on a verified register</b> "
						"and record why."
					).format(row.idx, frappe.bold(row.participant_name), frappe.bold(self.activity)),
					title=_("Payee not on the register"),
				)

			if not row.source_attendance_list:
				row.source_attendance_list = verified_attendees[row.participant]

	def get_verified_attendees(self):
		"""Participants on any verified register for this project, mapped to that register."""
		if not self.activity:
			return {}

		rows = frappe.get_all(
			"Activity Participant Entry",
			filters={
				"parenttype": "Activity Participant List",
				"attended": 1,
				"eligible_for_reimbursement": 1,
			},
			fields=["participant", "parent"],
		)

		verified_registers = set(
			frappe.get_all(
				"Activity Participant List",
				filters={"activity": self.activity, "docstatus": 1},
				pluck="name",
			)
		)

		return {
			row.participant: row.parent
			for row in rows
			if row.participant and row.parent in verified_registers
		}

	def apply_rate_basis(self):
		"""Compare each amount to the rate schedule and require a reason for any difference."""
		session_date = None
		if self.attendance_reference:
			session_date = frappe.db.get_value(
				"Activity Participant List", self.attendance_reference, "session_date"
			)

		schedule = get_rate_schedule(project=self.activity, on_date=session_date)

		for row in self.participants or []:
			rates = get_rates_for_location(schedule, row.location)
			scheduled_total = sum(flt(value) for value in rates.values())

			if not schedule or not scheduled_total:
				row.rate_basis = "Off-schedule"
			elif flt(row.amount) == flt(scheduled_total):
				row.rate_basis = "Schedule"
			else:
				row.rate_basis = "Adjusted"

			if row.rate_basis != "Schedule" and not (row.justification or "").strip():
				frappe.throw(
					_(
						"Row {0}: {1} is being paid {2}, which is not the scheduled rate for {3}. "
						"Record why on the row."
					).format(
						row.idx,
						frappe.bold(row.participant_name),
						frappe.bold(frappe.format_value(row.amount, {"fieldtype": "Currency"})),
						row.location or _("their location"),
					),
					title=_("Rate differs from the schedule"),
				)

	def set_totals(self):
		rows = self.participants or []
		self.total_amount = sum(flt(row.amount) for row in rows)
		self.total_paid = sum(flt(row.amount) for row in rows if row.payment_status == "Paid")

	def validate_against_advance(self):
		"""A list cannot pay out more than the float actually holds (F-04-E6).

		Two things are checked here and the order matters, because the second one used to answer
		for both. A float that has already been retired and closed is not a float that is merely
		too small, but the only objection raised was the arithmetic -- so attaching a list to a
		finished float reported "a float cannot pay out more than it holds", which reads as
		"find a bigger float" when the truth is "that float is done; this activity needs its own".
		"""
		if not self.employee_advance:
			return

		advance = frappe.db.get_value(
			"Employee Advance",
			self.employee_advance,
			["paid_amount", "claimed_amount", "workflow_state", "folt_project"],
			as_dict=True,
		)
		if not advance:
			return

		self.advance_disbursed = flt(advance.paid_amount)

		# A retired float pays out nothing further, whatever its headroom looks like. Both
		# conditions are needed: `Closed` is the Head of Finance's decision, and a claim against
		# the float is the fact underneath it -- a float can be accounted for before anybody
		# closes it (float_lifecycle derives Accounted from claimed_amount).
		if advance.workflow_state == "Closed":
			frappe.throw(
				_(
					"Float {0} has been closed, so nothing further can be paid from it. If this activity "
					"needs a payout, it needs its own float — raise one from its requisition."
				).format(frappe.bold(self.employee_advance)),
				title=_("Float is closed"),
			)

		if not self.advance_disbursed:
			return

		other_lists = frappe.get_all(
			"Participant Reimbursement List",
			filters={
				"employee_advance": self.employee_advance,
				"docstatus": ["<", 2],
				"name": ["!=", self.name],
			},
			fields=["name", "total_amount", "workflow_state"],
		)

		already = sum(flt(row.total_amount) for row in other_lists)
		committed = flt(self.total_amount) + already

		if committed > self.advance_disbursed:
			# Name what is consuming the float. Being told the total is over by 140,500 is not
			# actionable on its own -- the question is always "spent on what?", and the answer is
			# usually one list somebody else raised.
			consumers = [
				_("{0} ({1}, {2})").format(
					frappe.utils.get_link_to_form("Participant Reimbursement List", row.name),
					row.workflow_state or _("Draft"),
					frappe.format_value(flt(row.total_amount), {"fieldtype": "Currency"}),
				)
				for row in other_lists
				if flt(row.total_amount)
			]

			message = [
				_("This list commits {0}, but float {1} has {2} disbursed and {3} of that is already committed — leaving {4}.").format(
					frappe.bold(frappe.format_value(flt(self.total_amount), {"fieldtype": "Currency"})),
					self.employee_advance,
					frappe.bold(frappe.format_value(self.advance_disbursed, {"fieldtype": "Currency"})),
					frappe.format_value(already, {"fieldtype": "Currency"}),
					frappe.bold(frappe.format_value(self.advance_disbursed - already, {"fieldtype": "Currency"})),
				)
			]
			if consumers:
				message.append(_("Already committed by: {0}").format(", ".join(consumers)))
			if not advance.folt_project:
				# The reason a register from an unrelated activity could reach this float at all:
				# set_activity_from_advance has nothing to scope by, so the project check that
				# annex 6.4.1 relies on never ran (see get_payable_floats).
				message.append(
					_("Note: float {0} carries no activity, so it is not scoped to a project. Set one on the float.").format(
						self.employee_advance
					)
				)

			frappe.throw("<br><br>".join(message), title=_("Exceeds the float"))


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_payable_floats(doctype, txt, searchfield, start, page_len, filters):
	"""Link query for `employee_advance`: floats that can still pay somebody.

	The field had no query at all, which is how a closed, fully-retired float came to be attached
	to a list for an unrelated activity -- `activity_chain.funded_floats` does filter properly,
	but it only serves the hand-off button, and nothing stopped a name being typed into the form.

	`docstatus` and `workflow_state` are set last and override whatever arrived, for the same
	reason `activity_participant_list.get_verified_registers` does it: this query exists to offer
	floats that are disbursed and not closed, and a draft or closed one must not reach the
	dropdown even if the caller asks for it.

	The caller passes `folt_project` only once the list knows its activity, which a new list does
	not -- so a blank list is offered every funded float, and one already scoped to a project is
	offered only that project's. A float carrying NO project is therefore not offered to a scoped
	list at all, and that is the intended answer: annex 6.4.1 makes the project the thing that
	keeps a register and a float on the same activity, and the remedy is to set the activity on
	the float rather than to widen this query. `validate_against_advance` says so in as many
	words when it meets one.
	"""
	applied = dict(filters or {})
	applied["docstatus"] = 1
	applied["workflow_state"] = ["in", FUNDED_FLOAT_STATES]

	return frappe.get_list(
		"Employee Advance",
		fields=["name", "employee_name", "paid_amount"],
		filters=applied,
		or_filters=[["name", "like", f"%{txt}%"], ["employee_name", "like", f"%{txt}%"]] if txt else None,
		order_by="posting_date desc",
		start=start,
		page_length=page_len,
		as_list=True,
	)


@frappe.whitelist()
def fetch_participants(reimbursement_list, register=None):
	"""Pull eligible attendees from a verified register into the list (W-04B derivation).

	Only attendees who attended, are eligible by category, and are not already on the
	list are added. Rates are proposed from the schedule that applies to the project.
	"""
	doc = frappe.get_doc("Participant Reimbursement List", reimbursement_list)
	doc.check_permission("write")

	register = register or doc.attendance_reference
	if not register:
		frappe.throw(_("Select the attendance register to fetch participants from."))

	source = frappe.get_doc("Activity Participant List", register)

	if source.activity != doc.activity:
		frappe.throw(
			_("Register {0} belongs to project {1}, not to {2}.").format(
				register, source.activity, doc.activity
			)
		)

	if source.docstatus != 1:
		frappe.throw(_("Register {0} is not verified yet.").format(register))

	# The list records the register it was derived from, so the derivation is visible on the
	# document rather than only in whoever pressed the button.
	if not doc.attendance_reference:
		doc.attendance_reference = source.name

	existing = {row.participant for row in doc.participants if row.participant}
	schedule = get_rate_schedule(project=doc.activity, on_date=source.session_date)

	added = 0
	skipped_ineligible = 0

	for row in source.participants:
		if not row.attended:
			continue

		if not row.eligible_for_reimbursement:
			skipped_ineligible += 1
			continue

		if row.participant and row.participant in existing:
			continue

		rates = get_rates_for_location(schedule, row.location)

		doc.append(
			"participants",
			{
				"participant": row.participant,
				"participant_name": row.participant_name,
				"mobile_number": row.mobile_number,
				"id_number": row.id_number,
				"location": row.location,
				"category": row.category,
				"source_attendance_list": source.name,
				"acknowledgement": "",
				"payment_status": "Pending",
				**{component: rates.get(component) for component in RATE_COMPONENTS},
			},
		)
		added += 1

	doc.save()

	return {
		"added": added,
		"skipped_ineligible": skipped_ineligible,
		"total_amount": doc.total_amount,
		"no_rate": not schedule,
	}
