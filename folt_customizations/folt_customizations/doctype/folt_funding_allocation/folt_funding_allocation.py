import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, fmt_money

from folt_customizations.masterdata import MasterRecord


class FoLTFundingAllocation(MasterRecord, Document):
	def validate(self):
		if flt(self.amount) <= 0:
			frappe.throw(_("An allocation sets some money aside: the amount must be more than nothing."), title=_("Check the amount"))
		self._check_within_the_source()
		super().validate()

	def _check_within_the_source(self):
		"""What a funding source gives out cannot exceed what it holds: the other live allocations
		against it (drafts, under review, active), plus this one, against its total."""
		total, currency = frappe.db.get_value("FoLT Grant Project", self.funding_source, ["total_amount", "grant_currency"]) or (0, None)
		if not flt(total):
			return
		others = frappe.db.sql(
			"""select coalesce(sum(amount), 0) from `tabFoLT Funding Allocation`
			   where funding_source = %s and name != %s and workflow_state not in ('Rejected', 'Archived', 'Inactive')""",
			(self.funding_source, self.name or ""),
		)[0][0]
		if flt(others) + flt(self.amount) > flt(total):
			frappe.throw(
				_("{0} holds {1}; {2} is already allocated, so at most {3} is left for this allocation.").format(
					frappe.bold(self.funding_source),
					fmt_money(total, currency=currency),
					fmt_money(others, currency=currency),
					fmt_money(max(0, flt(total) - flt(others)), currency=currency),
				),
				title=_("More than the funding source holds"),
			)
