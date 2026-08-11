import frappe
from frappe import _
from frappe.model.document import Document


class ParticipantRateSchedule(Document):
	def validate(self):
		seen = {}
		for row in self.rates or []:
			if row.location in seen:
				frappe.throw(
					_("Location {0} appears twice — rows {1} and {2}.").format(
						frappe.bold(row.location), seen[row.location], row.idx
					)
				)
			seen[row.location] = row.idx

		if self.valid_upto and self.valid_from and self.valid_upto < self.valid_from:
			frappe.throw(_("Valid up to cannot be earlier than valid from."))
