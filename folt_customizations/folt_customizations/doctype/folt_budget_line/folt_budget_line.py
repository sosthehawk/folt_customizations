from frappe.utils.nestedset import NestedSet

from folt_customizations.masterdata import MasterRecord


class FoLTBudgetLine(MasterRecord, NestedSet):
	pass
