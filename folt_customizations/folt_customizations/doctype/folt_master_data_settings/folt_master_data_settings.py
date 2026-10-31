import frappe
from frappe.model.document import Document


class FoLTMasterDataSettings(Document):
	def validate(self):
		seen = set()
		for rule in self.rules:
			if rule.master_doctype in seen:
				frappe.throw(frappe._("{0} has two rules.").format(rule.master_doctype))
			seen.add(rule.master_doctype)
			rule.code_prefix = (rule.code_prefix or "").strip()

	def on_update(self):
		# The approval rules are the master workflows: rewrite them to match.
		if self.flags.skip_workflow_rebuild:
			return
		from folt_customizations.masterdata import apply_master_workflows

		apply_master_workflows()
