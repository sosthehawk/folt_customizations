import frappe
from frappe.model.document import Document
from frappe.utils import flt

from folt_customizations.notifications import notify_committee_members
from folt_customizations.procurement import COMMITTEE_REVIEW_STATE, rfq_quotations

# The workflow state at which Head-of-Finance approval ("Intent to Award") is sought.
# Committee sign-off must be complete (quorum met) before the document may enter it.
PENDING_APPROVAL_STATE = "Pending Head of Finance Approval"

# The fields on each grid that belong to one named member, per table. Nobody may fill these in
# on somebody else's behalf -- see enforce_self_scoring. `member` itself is absent on purpose:
# who is on the committee is the preparer's business (enforce_committee_composition), and the
# derived columns of the scoring grid are read_only on the doctype for everybody already.
SELF_ONLY_FIELDS = {
	"members": ("reviewed", "score", "comments"),
	"quotation_scores": ("score", "comments"),
}


class ProcurementCommitteeEvaluation(Document):
	def validate(self):
		self.enforce_conflict_of_interest()
		self.enforce_committee_composition()
		self.sync_quotation_scores()
		# After the rebuild, so it judges the grid that is actually about to be stored.
		self.enforce_self_scoring()
		self.enforce_award()
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

	def enforce_self_scoring(self):
		"""Nobody may enter or alter a score, a comment or a sign-off in somebody else's name.

		This is what makes the grid evidence rather than a shared spreadsheet. Without it any
		holder of the Procurement Committee role can score for the whole committee -- the
		workflow hands that role write on the entire document while the evaluation sits in
		`Committee Reviewing` (fixtures/workflow.json: allow_edit) -- and, worse, tick another
		member's `reviewed` box and carry the quorum on their own. A score somebody else typed
		is not that member's opinion, and an award recommendation built out of them is not a
		committee decision.

		Enforced here rather than by permission because the rule is about *which rows* changed,
		and Frappe's permissions cannot see inside a child table: permlevel hides a field from
		everyone at once, and `if_owner` asks who owns the evaluation, not whose row it is. So
		the check is a comparison against the stored document -- what changed, and whose it was.

		Administrator is exempt. Patches, fixtures and the e2e scripts save these documents with
		no member session behind them at all, and a mis-keyed score that has to be corrected
		after the fact is corrected from the console, deliberately and traceably, rather than by
		somebody clicking in a form.
		"""
		if frappe.session.user == "Administrator":
			return

		before = self.get_doc_before_save()
		for table, fields in SELF_ONLY_FIELDS.items():
			stored = {}
			for row in (before.get(table) if before else None) or []:
				stored.setdefault(row.name, row)

			for row in self.get(table) or []:
				if row.member == frappe.session.user:
					continue
				old = stored.get(row.name)
				# `old` missing means a row that did not exist before this save. On a new
				# evaluation, or a row appended by hand, the same rule applies: an empty row is
				# fine (the rebuild creates one per member per bid), a pre-filled one is not.
				if any(not _unchanged(row.get(field), old.get(field) if old else None) for field in fields):
					_throw_not_your_row(table, row)

	def enforce_committee_composition(self):
		"""A member of the committee may not change who else is on it.

		The other half of self-scoring, one step earlier. The quorum is counted against the
		member list -- `required = self.quorum or total` in enforce_quorum -- so a member who can
		drop the colleagues who have not signed yet reaches Intent to Award on their own vote,
		without ever touching anybody else's score. And the list is editable by them: from
		`Committee Reviewing` onwards the workflow gives the Procurement Committee role write on
		the whole document.

		The test is membership, not role, because that is what the conflict is: the people being
		asked to score cannot pick who scores alongside them. Whoever prepares the evaluation is
		barred from sitting on it anyway (enforce_conflict_of_interest), so this never gets in
		the way of a buyer correcting the list, and a first save has no previous list to compare.
		"""
		if frappe.session.user == "Administrator":
			return

		before = self.get_doc_before_save()
		if not before:
			return

		was = [row.member for row in before.get("members") or []]
		now = [row.member for row in self.members or []]
		if was == now or frappe.session.user not in set(was) | set(now):
			return

		frappe.throw(
			frappe._("The committee cannot be changed by a member of it. Ask the buyer who raised this evaluation, or a System Manager, to add or remove members."),
			title=frappe._("Committee is fixed"),
		)

	def enforce_award(self):
		"""The recommendation has to name a bid from this competition, and name one at all.

		Two rules, one method, because they are two halves of what "Intent to Award" means:

		*The winning bid is one of the bids.* `recommended_supplier_quotation` is a plain link to
		Supplier Quotation, so before this it would take any quotation in the system -- including
		one from a different RFQ that no member of this committee ever scored. The supplier is
		derived from it rather than asked for again, which is also how the two stop disagreeing:
		a recommendation naming supplier A and A's competitor's quotation is not a typo anybody
		would spot on the printed Intent to Award.

		*There is a winning bid before the award is sought.* The Head of Finance's step is
		approving an award; an evaluation reaching it with the recommendation blank asks them to
		approve nothing, and procurement_chain.make_award_order then has no bid to raise the
		order from. Enforced only on entry to that state, so the committee can score and save
		for as long as it takes to decide.
		"""
		if self.recommended_supplier_quotation:
			bids = {row.supplier_quotation: row.supplier for row in self.quotation_scores or []}
			if not bids:
				bids = {
					row["supplier_quotation"]: row["supplier"]
					for row in rfq_quotations(self.request_for_quotation)
				}

			if self.recommended_supplier_quotation not in bids:
				frappe.throw(
					frappe._("{0} is not one of the bids received against {1}, so it cannot be awarded on this evaluation.").format(
						frappe.bold(self.recommended_supplier_quotation),
						frappe.bold(self.request_for_quotation or frappe._("this RFQ")),
					),
					title=frappe._("Not a bid in this competition"),
				)

			self.recommended_supplier = bids[self.recommended_supplier_quotation]

		elif self.workflow_state == PENDING_APPROVAL_STATE:
			frappe.throw(
				frappe._("Name the winning bid before seeking award approval -- the Head of Finance is being asked to approve an award, and the order is raised from the bid it names."),
				title=frappe._("No award to approve"),
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


def _throw_not_your_row(table, row):
	"""Refuse the save, naming whose row it is -- which is the part the reader can act on."""
	if table == "quotation_scores":
		message = frappe._("The score for {0} from {1} is {2}'s to enter -- each member fills in their own row.")
		message = message.format(row.supplier_quotation, row.supplier, row.member)
	else:
		message = frappe._("The sign-off for {0} is theirs to give -- each member signs their own review.")
		message = message.format(row.member)
	frappe.throw(message, title=frappe._("Not your row"))


def _unchanged(new, old):
	"""Whether a grid cell holds what it held before, treating blank and zero as one value.

	Frappe hands back None for a Float never touched and "" for a cleared Small Text, and a
	round-trip through the form turns one into the other -- so a plain `!=` would read an
	untouched row as an edit and refuse a save that changed nothing.
	"""
	if new in (None, "") and old in (None, ""):
		return True
	if isinstance(new, str) or isinstance(old, str):
		return (new or "").strip() == (old or "").strip()
	# A zero therefore reads the same as a blank, which is the only workable reading -- an
	# untouched Float comes back as 0.0 and there is nothing to tell the two apart. It costs
	# nothing: a nought planted in another member's row changes no ranking, while clearing a
	# sign-off (1 -> 0) or a real score still shows up as the change it is.
	return flt(new) == flt(old)
