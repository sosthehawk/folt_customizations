import frappe
from frappe.utils.nestedset import NestedSet

from folt_customizations.masterdata import MasterRecord


class FoLTLocation(MasterRecord, NestedSet):
	def validate(self):
		# A place typed the old way, with its region as text, goes under that region's node.
		if not self.parent_folt_location and self.parent_region and self.parent_region != self.location_name:
			if frappe.db.exists("FoLT Location", self.parent_region):
				self.parent_folt_location = self.parent_region
		super().validate()
