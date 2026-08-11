from frappe.model.document import Document

from folt_customizations.participants import normalise_mobile


class FoLTParticipant(Document):
	def validate(self):
		# The mobile number is the payout destination and the key that recognises a repeat
		# participant across activities, so it is normalised once here rather than at every
		# point of use (annex W-04A, F-04A-V4).
		self.mobile_number = normalise_mobile(self.mobile_number, label=self.participant_name)
