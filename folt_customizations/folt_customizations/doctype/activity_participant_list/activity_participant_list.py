import frappe
from frappe import _
from frappe.model.document import Document

from folt_customizations.participants import (
	ELIGIBLE_CATEGORIES,
	check_duplicates,
	normalise_mobile,
)


class ActivityParticipantList(Document):
	"""The attendance register — the source record for who took part in an activity.

	Specified as W-04A in Module 1 Annex A. Nothing may be reimbursed that is not on a
	verified register for the same project, so the validation here is what makes the
	reimbursement list safe to derive.
	"""

	def validate(self):
		self.normalise_rows()
		check_duplicates(self.participants or [], _("attendance register"))
		self.link_or_create_participants()
		self.set_totals()

	def before_submit(self):
		if not self.participants:
			frappe.throw(_("A register cannot be verified with no attendees."))

		# The signed sheet is asked for, not demanded. It used to be a throw here, and what that
		# cost was the ordinary case: the activity happened, the attendees are keyed in and
		# correct, and the scanned sheet is still in somebody's phone or with the field officer.
		# Blocking verification on it stopped the whole chain -- the reimbursement list derives
		# from a *verified* register -- over a document that changes nothing about who attended.
		# So it is a standing reminder on a register that carries no sheet, and the checklist on
		# the form says the same thing before anyone presses the button (document_guide.DOCUMENTS).
		if not self.attendance_sheet:
			frappe.msgprint(
				_("This register is being verified without the signed attendance sheet. Attach it when it arrives — the field stays editable after verification."),
				title=_("No signed sheet on file"),
				indicator="orange",
			)

	def normalise_rows(self):
		for row in self.participants or []:
			row.mobile_number = normalise_mobile(row.mobile_number, label=row.participant_name)

			# Category drives eligibility, but the flag stays editable so that excluding
			# someone is a recorded decision rather than a silent omission (F-04-D2).
			if row.get("__islocal") or row.eligible_for_reimbursement is None:
				row.eligible_for_reimbursement = 1 if row.category in ELIGIBLE_CATEGORIES else 0

			if not row.attended:
				row.eligible_for_reimbursement = 0

	def link_or_create_participants(self):
		"""Match each attendee to the participant master, creating it where new.

		Matching is on the mobile number, so a person who returns to a second activity
		is the same record rather than a re-keyed string (F-04A-V4).
		"""
		for row in self.participants or []:
			if row.participant:
				continue

			if not row.mobile_number:
				# A cash payee with no mobile destination is permitted but has no natural
				# key, so no master record is created for them here (F-04A-E1).
				continue

			existing = frappe.db.get_value(
				"FoLT Participant", {"mobile_number": row.mobile_number}, "name"
			)

			if existing:
				row.participant = existing
				continue

			participant = frappe.get_doc(
				{
					"doctype": "FoLT Participant",
					"participant_name": row.participant_name,
					"mobile_number": row.mobile_number,
					"id_number": row.id_number,
					"location": row.location,
					"gender": row.gender,
					"is_pwd": row.is_pwd,
					"photo_consent": row.photo_consent,
				}
			).insert(ignore_permissions=True)

			row.participant = participant.name

	def set_totals(self):
		rows = self.participants or []
		self.total_attendees = sum(1 for row in rows if row.attended)
		self.total_eligible = sum(1 for row in rows if row.attended and row.eligible_for_reimbursement)

	def on_update_after_submit(self):
		# Only a change to WHO attended can orphan a payee. Now that the signed sheet may be
		# attached after verification, an edit that touches nothing but the evidence is the
		# ordinary case, and warning about derived lists there would train people to dismiss the
		# warning that matters.
		if self.attendees_changed():
			self.revalidate_derived_lists()

	def attendees_changed(self) -> bool:
		before = self.get_doc_before_save()
		if not before:
			return True

		def shape(doc):
			return [
				(row.participant, row.participant_name, row.mobile_number, row.attended, row.eligible_for_reimbursement)
				for row in doc.participants or []
			]

		return shape(before) != shape(self)

	def on_cancel(self):
		self.revalidate_derived_lists(cancelled=True)

	def revalidate_derived_lists(self, cancelled=False):
		"""Flag reimbursement lists already derived from this register (F-04A-E6).

		Amending a register after money has been budgeted against it can orphan a payee,
		so anything derived from it is surfaced rather than left to be discovered at
		retirement.
		"""
		derived = frappe.get_all(
			"Participant Reimbursement List",
			filters={"attendance_reference": self.name, "docstatus": ["<", 2]},
			pluck="name",
		)

		if not derived:
			return

		frappe.msgprint(
			_("This register has changed. Re-check the reimbursement lists derived from it: {0}").format(
				", ".join(frappe.utils.get_link_to_form("Participant Reimbursement List", d) for d in derived)
			),
			title=_("Cancelled register") if cancelled else _("Register amended"),
			indicator="orange",
		)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_verified_registers(doctype, txt, searchfield, start, page_len, filters):
	"""Link query used by the reimbursement list: verified registers, this project only.

	The filters are handed straight to `get_all` rather than pasted into SQL, and that is the
	whole point of the shape of this function. A link field does not post the filters the form
	script set: frappe normalises each one into an `[operator, value]` pair on its way to
	`search_link`, so `{"activity": "PROJ-0005"}` arrives as `{"activity": ["=", "PROJ-0005"]}`.
	Bound into a query as a parameter, a two-element list is rendered by MySQLdb as a row
	constructor and MariaDB rejects the comparison outright -- "Illegal parameter data types
	varchar and row for operation '='" -- so picking a register failed with a traceback rather
	than a wrong result. `get_all` is built for that form and takes either.

	`docstatus` is set last and deliberately overrides whatever arrived: this query exists to
	offer verified registers, and a draft one must never reach the dropdown even if the caller
	asks for it. Nothing may be reimbursed that is not on a verified register (see the class
	docstring), and this is where that starts.
	"""
	applied = dict(filters or {})
	applied["docstatus"] = 1

	return frappe.get_all(
		"Activity Participant List",
		fields=["name", "activity_title", "session_date"],
		filters=applied,
		or_filters=[["name", "like", f"%{txt}%"], ["activity_title", "like", f"%{txt}%"]] if txt else None,
		order_by="session_date desc",
		start=start,
		page_length=page_len,
		as_list=True,
	)
