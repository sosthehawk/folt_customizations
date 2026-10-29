"""Salary slips are confidential: without a payroll role, a person reads only their own.

HRMS gives the Employee role read on Salary Slip with no if_owner and no User Permission, and FoLT's
employees carry create_user_permission only once a login is linked -- so on this site every
employee could read every slip: in the Desk, through /printview, and through /api/resource. The
BFF scoped its own endpoints to the session employee, but hiding a thing in one client is not
protecting it. These two hooks make Frappe itself refuse.

  - salary_slip_query (permission_query_conditions): lists, counts and reports get
    `employee IN (the caller's own Employee records)` added.
  - salary_slip_permission (has_permission): one slip, for print, PDF, form and API reads.

PAYROLL_ROLES read every slip. They are the BFF's payroll guard (guard.PayrollRoles) and must stay
the same list, plus System Manager, which administers the site; Administrator always passes.
Background jobs and payroll processing run with ignore_permissions or as Administrator, so neither
is affected.
"""

import frappe

PAYROLL_ROLES = {"Finance Assistant", "Finance Officer", "Executive Director", "HR User", "HR Manager", "System Manager"}


def is_payroll_user(user=None):
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return bool(PAYROLL_ROLES.intersection(frappe.get_roles(user)))


def _own_employees(user):
	return frappe.get_all("Employee", filters={"user_id": user}, pluck="name")


def salary_slip_query(user=None):
	user = user or frappe.session.user
	if is_payroll_user(user):
		return ""
	own = _own_employees(user)
	if not own:
		return "1=0"
	return "`tabSalary Slip`.`employee` in ({})".format(", ".join(frappe.db.escape(e) for e in own))


def salary_slip_permission(doc, ptype=None, user=None, debug=False):
	# frappe 16's has_controller_permissions can only deny, and it treats any falsy answer --
	# None included -- as a denial. True means "no objection; the role permissions decide".
	user = user or frappe.session.user
	if is_payroll_user(user):
		return True
	if isinstance(doc, str):
		doc = frappe.get_doc("Salary Slip", doc)
	return doc.employee in _own_employees(user)
