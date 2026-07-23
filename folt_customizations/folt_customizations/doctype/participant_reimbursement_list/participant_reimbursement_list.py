import frappe
from frappe.model.document import Document


class ParticipantReimbursementList(Document):
	def validate(self):
		self.total_amount = sum((row.amount or 0) for row in self.participants or [])
