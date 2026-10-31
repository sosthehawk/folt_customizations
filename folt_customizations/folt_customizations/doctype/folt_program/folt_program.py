import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate

from folt_customizations.masterdata import MasterRecord


class FoLTProgram(MasterRecord, Document):
	def before_insert(self):
		settings = frappe.get_cached_doc("FoLT Master Data Settings")
		if not self.category and settings.default_program_category:
			self.category = settings.default_program_category
		if not self.program_status and settings.default_program_status:
			self.program_status = settings.default_program_status
		super().before_insert()

	def validate(self):
		if self.start_date and self.end_date and getdate(self.end_date) < getdate(self.start_date):
			frappe.throw(_("A programme cannot end before it starts."), title=_("Check the dates"))
		super().validate()
