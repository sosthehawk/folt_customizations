import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from folt_customizations.participants import (
	RATE_COMPONENTS,
	check_duplicates,
	get_rate_schedule,
	get_rates_for_location,
	normalise_mobile,
)

ACKNOWLEDGED = ("Signature", "Thumbprint")


class ParticipantReimbursementList(Document):
	"""Who was paid from a float — derived from the attendance register, never typed.

	Specified as W-04B in Module 1 Annex A. The register (Activity Participant List) is
	the source; this document is scoped by the same Project, and every payee must trace
	back to a verified attendee on that project or carry a recorded justification.
	"""

	def validate(self):
		self.set_activity_from_advance()
		self.validate_register_project()
		self.normalise_rows()
		check_duplicates(self.participants or [], _("reimbursement list"))
		self.validate_rows_against_register()
		self.apply_rate_basis()
		self.set_totals()
		self.validate_against_advance()

	def before_update_after_submit(self):
		"""Payouts are recorded against a verified list, so the paid roll-up and the
		acknowledgement rule have to hold here too — validate() does not run after submit.
		"""
		self.normalise_rows()
		self.set_totals()

	def before_submit(self):
		if not self.participants:
			frappe.throw(_("A reimbursement list cannot be verified with no participants."))

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
		"""A list cannot pay out more than the float actually holds (F-04-E6)."""
		if not self.employee_advance:
			return

		self.advance_disbursed = flt(
			frappe.db.get_value("Employee Advance", self.employee_advance, "paid_amount")
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
			pluck="total_amount",
		)

		committed = flt(self.total_amount) + sum(flt(amount) for amount in other_lists)

		if committed > self.advance_disbursed:
			frappe.throw(
				_(
					"This list commits {0} against float {1}, which has only {2} disbursed. "
					"A float cannot pay out more than it holds."
				).format(
					frappe.bold(frappe.format_value(committed, {"fieldtype": "Currency"})),
					self.employee_advance,
					frappe.bold(frappe.format_value(self.advance_disbursed, {"fieldtype": "Currency"})),
				),
				title=_("Exceeds the float"),
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
