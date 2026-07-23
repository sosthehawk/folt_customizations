import frappe
from frappe.model.document import Document

# The workflow state at which Head-of-Finance approval ("Intent to Award") is sought.
# Committee sign-off must be complete (quorum met) before the document may enter it.
PENDING_APPROVAL_STATE = "Pending Head of Finance Approval"


class ProcurementCommitteeEvaluation(Document):
	def validate(self):
		self.enforce_conflict_of_interest()
		if self.workflow_state == PENDING_APPROVAL_STATE:
			self.enforce_quorum()

	def enforce_conflict_of_interest(self):
		"""FoLT rule: the requester may not sit on the committee that evaluates their RFQ."""
		if not self.requested_by:
			return
		for row in self.members or []:
			if row.member and row.member == self.requested_by:
				frappe.throw(
					frappe._("Conflict of interest: the requester ({0}) cannot be a committee member.").format(
						self.requested_by
					)
				)

	def enforce_quorum(self):
		"""Block the move to Intent-to-Award until a quorum of members has signed."""
		total = len(self.members or [])
		if not total:
			frappe.throw(frappe._("Add committee members before seeking award approval."))
		signed = sum(1 for row in self.members if row.reviewed)
		required = self.quorum or total  # 0 means "all members must sign"
		if signed < required:
			frappe.throw(
				frappe._("Quorum not met: {0} of {1} required members have signed off.").format(signed, required)
			)
