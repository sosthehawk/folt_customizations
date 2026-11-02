from frappe.model.document import Document


class FoLTProcurementSettings(Document):
	"""Read by purchase_order.approval_tier (and, later, the match tolerance and reminders)."""
