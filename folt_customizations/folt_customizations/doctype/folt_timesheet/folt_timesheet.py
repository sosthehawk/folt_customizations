from frappe.model.document import Document


class FoLTTimesheet(Document):
	"""One employee's month, as confirmed and signed off. Written only by hr_timesheet.py, which
	builds the days from attendance, leave and the holiday list; nobody types a timesheet."""

	pass
