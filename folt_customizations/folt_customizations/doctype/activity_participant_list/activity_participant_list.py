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
		# The register is the evidence that the activity happened and who was there;
		# verifying it without the signed sheet would make it an assertion instead.
		if not self.attendance_sheet:
			frappe.throw(
				_("Attach the signed attendance sheet before this register is verified."),
				title=_("Evidence required"),
			)

		if not self.participants:
			frappe.throw(_("A register cannot be verified with no attendees."))

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
		self.revalidate_derived_lists()

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
def get_verified_registers(doctype, txt, searchfield, start, page_len, filters):
	"""Link query used by the reimbursement list: verified registers, this project only."""
	project = (filters or {}).get("activity")

	conditions = ["docstatus = 1"]
	values = {"txt": f"%{txt or ''}%", "start": start, "page_len": page_len}

	if project:
		conditions.append("activity = %(project)s")
		values["project"] = project

	return frappe.db.sql(
		f"""
		select name, activity_title, session_date
		from `tabActivity Participant List`
		where {' and '.join(conditions)}
			and (name like %(txt)s or activity_title like %(txt)s)
		order by session_date desc
		limit %(start)s, %(page_len)s
		""",
		values,
	)
