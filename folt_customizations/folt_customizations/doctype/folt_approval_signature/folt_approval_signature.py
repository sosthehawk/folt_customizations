from frappe.model.document import Document


class FoLTApprovalSignature(Document):
	"""One step a workflow document took: who moved it, in what capacity, when, and their signature.

	Written only by approval_signatures.record, from inside the save that made the move, so a row
	exists exactly when the step stuck. Nobody edits one afterwards; see that module.
	"""
