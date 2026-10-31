import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, nowdate

from folt_customizations.masterdata import MasterRecord


class FoLTReportingPeriod(MasterRecord, Document):
	def validate(self):
		if getdate(self.end_date) < getdate(self.start_date):
			frappe.throw(_("A reporting period cannot end before it starts."), title=_("Check the dates"))
		if getdate(self.deadline) < getdate(self.end_date):
			frappe.throw(_("The report cannot be due before the period it reports on has ended."), title=_("Check the dates"))
		# A report moved back to Open after its due date is Overdue at once, not tomorrow.
		if self.reporting_status == "Open" and getdate(self.deadline) < getdate(nowdate()):
			self.reporting_status = "Overdue"
		super().validate()
