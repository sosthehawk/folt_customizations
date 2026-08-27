import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate


class ActivityRequisition(Document):
	"""Step 1 of the Finance SOP: the activity, its budget and its funding, agreed once.

	Everything the rest of the chain needs is decided here -- the activity and its dates, the
	budget line and donor, whether a cash float is needed and who will hold it. The float
	request, the attendance register and the expense report are all filled from these fields
	rather than asked for again, so a value that is wrong here is wrong in one place and
	corrected in one place. See activity_chain.py for the hand-offs.
	"""

	def validate(self):
		self.set_defaults()
		self.validate_dates()
		self.validate_float()

	def before_submit(self):
		"""Approval is what turns a requisition into an activity everything else can point at.

		Done here rather than in `on_submit` so the project lands on the document in the same
		save as the approval, with no second write after it -- and so the requisition is never
		approved while the thing it authorises does not exist.
		"""
		self.project = self.project or self.open_activity_project()

	def set_defaults(self):
		if not self.company:
			self.company = frappe.defaults.get_user_default("Company") or frappe.db.get_default("company")

		if not self.requested_by:
			self.requested_by = frappe.db.get_value(
				"Employee", {"user_id": frappe.session.user, "status": "Active"}
			)

		# The commonest case is that the person asking for the float is the one who will carry
		# it; naming them twice is a keystroke that adds nothing.
		if self.float_required and not self.float_holder:
			self.float_holder = self.requested_by

		if self.float_required and not self.float_amount:
			self.float_amount = self.budget_amount

		if not self.activity_end_date:
			self.activity_end_date = self.activity_date

	def validate_dates(self):
		if self.activity_end_date and getdate(self.activity_end_date) < getdate(self.activity_date):
			frappe.throw(
				_("The activity cannot end on {0}, before it starts on {1}.").format(
					frappe.bold(frappe.format_value(self.activity_end_date, {"fieldtype": "Date"})),
					frappe.bold(frappe.format_value(self.activity_date, {"fieldtype": "Date"})),
				),
				title=_("Dates the wrong way round"),
			)

	def validate_float(self):
		if not self.float_required:
			return

		# A float larger than the activity's own budget is the one arithmetic error in this
		# document that nothing downstream can catch: the float request inherits the figure, and
		# by the time it is over-spent the money has gone out.
		if flt(self.float_amount) > flt(self.budget_amount):
			frappe.throw(
				_("The float of {0} is more than the activity's budget of {1}.").format(
					frappe.bold(frappe.format_value(self.float_amount, {"fieldtype": "Currency"})),
					frappe.bold(frappe.format_value(self.budget_amount, {"fieldtype": "Currency"})),
				),
				title=_("Float exceeds the budget"),
			)

	def open_activity_project(self) -> str:
		"""The Project that represents this activity, reused if it already exists.

		Every document after step 1 is scoped by a Project -- the register, the reimbursement
		list and the float all carry it, and the float's three-day accountability deadline is
		counted from its end date. Asking finance to create one by hand after each approval was
		the join in the chain most often left undone, and a float with no project has no
		deadline at all.
		"""
		existing = frappe.db.get_value("Project", {"project_name": self.activity_program}, "name")
		if existing:
			return existing

		return frappe.get_doc(
			{
				"doctype": "Project",
				"project_name": self.activity_program,
				"status": "Open",
				"company": self.company,
				"expected_start_date": self.activity_date,
				"expected_end_date": self.activity_end_date or self.activity_date,
				"cost_center": self.cost_center,
			}
		).insert(ignore_permissions=True).name
