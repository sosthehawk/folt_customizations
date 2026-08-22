import frappe
from frappe.model.document import Document

from folt_customizations.notifications import notify_committee_members
from folt_customizations.procurement import COMMITTEE_REVIEW_STATE, rfq_quotations

# The workflow state at which Head-of-Finance approval ("Intent to Award") is sought.
# Committee sign-off must be complete (quorum met) before the document may enter it.
PENDING_APPROVAL_STATE = "Pending Head of Finance Approval"


class ProcurementCommitteeEvaluation(Document):
	def validate(self):
		self.enforce_conflict_of_interest()
		self.sync_quotation_scores()
		if self.workflow_state == PENDING_APPROVAL_STATE:
			self.enforce_quorum()

	def on_update(self):
		# Notify on *entry* to the review state, not on every save while it sits there --
		# otherwise a member saving their own score re-notifies the rest of the committee.
		if self.workflow_state == COMMITTEE_REVIEW_STATE and self.has_value_changed("workflow_state"):
			notify_committee_members(self)

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

	def sync_quotation_scores(self):
		"""Give every committee member a row to score against every quotation on the RFQ.

		Derived from two fields the committee already fills in -- the RFQ and the member list --
		so nobody copies quotation numbers across by hand. The form script fills the same grid on
		the spot when the RFQ is picked (procurement_committee_evaluation.js); this is the
		authoritative pass, and it runs on every save rather than only on a change of RFQ because
		a late bid or a member added mid-evaluation has to appear without anyone re-picking
		anything.

		Scores and comments already entered are matched back by (member, quotation), so a rebuild
		never costs the committee its work. What a rebuild does drop is anything the two source
		fields no longer justify: rows for a member who has been taken off the committee, for a
		quotation that has been cancelled, or a line somebody typed into the grid by hand. The
		grid is derived data -- the RFQ and the member list are the only way to change it.
		"""
		quotations = rfq_quotations(self.request_for_quotation)
		# dict.fromkeys, not set(): the grid should follow the order the committee was entered in.
		members = list(dict.fromkeys(row.member for row in (self.members or []) if row.member))

		scored = {}
		for row in self.quotation_scores or []:
			scored.setdefault((row.member, row.supplier_quotation), row)

		rows = []
		for member in members:
			for quotation in quotations:
				row = scored.get((member, quotation.supplier_quotation))
				if row is None:
					row = self.append("quotation_scores", {})
				row.member = member
				row.supplier = quotation.supplier
				row.supplier_quotation = quotation.supplier_quotation
				row.quotation_amount = quotation.grand_total
				row.currency = quotation.currency
				row.valid_till = quotation.valid_till
				row.idx = len(rows) + 1
				rows.append(row)

		# Replacing the whole table is what drops the rows not rebuilt above; the ones that were
		# keep their row name, so an entered score keeps its own edit history rather than being
		# deleted and re-inserted on every save.
		self.set("quotation_scores", rows)

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
