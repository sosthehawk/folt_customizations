"""Onboarding pay (ADR-009 phase 5): putting an employee on pay, leave and the onboarding checklist
without the Desk. Served through hr_settings_portal (the HR settings edge), run as the user.

Four parts, each ERPNext's own record, each optional and done in any order (the user's choice: all
four):

* PAY -- a submitted Salary Structure Assignment. The structure and base come from the employee's
  grade (Employee Grade.default_salary_structure, default_base_pay) when it has them; the start is
  the joining date. Payroll figures: an editor who also holds a payroll role (salary_slip_access).
* LEAVE AND HOLIDAYS -- a submitted Leave Policy Assignment (HRMS makes the allocations from it) for
  the company's leave period (HRMS pro-rates it for a mid-period joiner), and, only when the employee keeps a different calendar from the
  company's, a Holiday List Assignment of their own.
* SALARY PAYMENT DETAILS -- Employee.salary_mode, bank_name, bank_ac_no, iban. Those four fields sit at
  permlevel 1 (a Property Setter fixture): payroll roles read them, HR Manager and System Manager
  change them (permissions.PERMLEVEL_1_PERMISSIONS), so an employee's own login and every other role
  no longer see anyone's bank details, here or in the Desk. This module asks the same before it
  answers them.
* ONBOARDING CHECKLIST -- ERPNext's Employee Onboarding from an Employee Onboarding Template: a
  project with one task per activity, assigned to the activity's user or role. HRMS requires a Job
  Applicant and an accepted Job Offer behind every onboarding (both are mandatory fields, and the
  project is named after the applicant). FoLT does not recruit in ERPNext, so when the employee has
  no applicant, starting the checklist makes the two from the employee record -- an Accepted
  applicant and a submitted, Accepted offer dated the joining date -- and links the applicant to the
  employee, which is how HRMS itself ties them (Employee.job_applicant).

Every write is followed by the employee's whole onboarding read again, so the screen redraws from one
answer.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

PAYMENT_FIELDS = ("salary_mode", "bank_name", "bank_ac_no", "iban")
SALARY_MODES = ("Bank", "Cash", "Cheque")
STEPS = ("pay", "leave", "holidays", "payment", "checklist", "activity")
MAX_LIST = 500


def _portal():
	from folt_customizations import hr_settings_portal

	return hr_settings_portal


def _payroll():
	from folt_customizations.salary_slip_access import is_payroll_user

	return is_payroll_user()


def _s(value):
	return str(value) if value else None


def _employee(employee):
	frappe.has_permission("Employee", "read", employee, throw=True)
	emp = frappe.db.get_value(
		"Employee",
		employee,
		["name", "employee_name", "company", "department", "designation", "grade", "date_of_joining", "status",
		 "job_applicant", "prefered_email", "company_email", "personal_email", "user_id"],
		as_dict=True,
	)
	if not emp:
		frappe.throw(_("Employee {0} not found.").format(employee), frappe.DoesNotExistError)
	return emp


def _email(emp):
	return emp.prefered_email or emp.company_email or emp.personal_email or emp.user_id


# --- what each part holds today -----------------------------------------------------------------


def _pay(emp, payroll):
	today = getdate(nowdate())
	rows = frappe.get_all(
		"Salary Structure Assignment",
		filters={"employee": emp.name, "docstatus": 1},
		fields=["name", "salary_structure", "from_date", "base", "variable", "income_tax_slab", "currency"],
		order_by="from_date desc",
	)
	current = next((r for r in rows if getdate(r.from_date) <= today), None) or (rows[-1] if rows else None)
	grade = frappe.db.get_value("Employee Grade", emp.grade, ["default_salary_structure", "default_base_pay"], as_dict=True) if emp.grade else None
	structure = grade.default_salary_structure if grade and grade.default_salary_structure else None
	if not structure:
		active = frappe.get_all("Salary Structure", filters={"docstatus": 1, "is_active": "Yes", "company": emp.company}, pluck="name")
		structure = active[0] if len(active) == 1 else None

	def shown(r):
		out = {"name": r.name, "salary_structure": r.salary_structure, "from_date": _s(r.from_date), "income_tax_slab": r.income_tax_slab, "currency": r.currency}
		out.update({"base": flt(r.base), "variable": flt(r.variable)} if payroll else {"base": None, "variable": None})
		return out

	return {
		"current": shown(current) if current else None,
		"assignments": len(rows),
		"suggested": {
			"salary_structure": structure,
			"base": flt(grade.default_base_pay) if payroll and grade and grade.default_base_pay else None,
			"from_date": _s(emp.date_of_joining),
			"from_grade": bool(grade and grade.default_salary_structure),
		},
	}


def _leave(emp):
	today = nowdate()
	period = frappe.get_all(
		"Leave Period",
		filters={"company": emp.company, "is_active": 1, "from_date": ["<=", today], "to_date": [">=", today]},
		fields=["name", "from_date", "to_date"],
		order_by="from_date desc",
		limit=1,
	)
	lpa = frappe.get_all(
		"Leave Policy Assignment",
		filters={"employee": emp.name, "docstatus": 1, "effective_from": ["<=", today], "effective_to": [">=", today]},
		fields=["name", "leave_policy", "effective_from", "effective_to"],
		limit=1,
	)
	allocations = frappe.get_all(
		"Leave Allocation",
		filters={"employee": emp.name, "docstatus": 1, "from_date": ["<=", today], "to_date": [">=", today]},
		fields=["name", "leave_type", "total_leaves_allocated", "leave_policy_assignment"],
		order_by="leave_type asc",
	)
	return {
		"leave_period": {"name": period[0].name, "from_date": _s(period[0].from_date), "to_date": _s(period[0].to_date)} if period else None,
		"policy_assignment": {"name": lpa[0].name, "leave_policy": lpa[0].leave_policy, "effective_from": _s(lpa[0].effective_from), "effective_to": _s(lpa[0].effective_to)} if lpa else None,
		"allocations": [{"name": a.name, "leave_type": a.leave_type, "days": flt(a.total_leaves_allocated)} for a in allocations],
	}


def _holidays(emp):
	from erpnext.setup.doctype.employee.employee import get_holiday_list_for_employee

	own = frappe.get_all(
		"Holiday List Assignment",
		filters={"applicable_for": "Employee", "assigned_to": emp.name, "docstatus": 1},
		fields=["name", "holiday_list", "from_date"],
		order_by="from_date desc",
		limit=1,
	)
	try:
		in_force = get_holiday_list_for_employee(emp.name, raise_exception=False)
	except Exception:  # noqa: BLE001 -- no list at all is an answer, not an error
		in_force = None
	return {
		"holiday_list": in_force or None,
		"own": {"name": own[0].name, "holiday_list": own[0].holiday_list, "from_date": _s(own[0].from_date)} if own else None,
	}


def _payment(emp, payroll):
	if not payroll:
		return None
	row = frappe.db.get_value("Employee", emp.name, list(PAYMENT_FIELDS), as_dict=True) or {}
	return {f: row.get(f) or None for f in PAYMENT_FIELDS}


def _payment_done(p):
	if not p or not p.get("salary_mode"):
		return False
	return p["salary_mode"] != "Bank" or bool(p.get("bank_name") and p.get("bank_ac_no"))


def _checklist(emp):
	name = frappe.db.get_value("Employee Onboarding", {"employee": emp.name, "docstatus": ["!=", 2]}, "name", order_by="creation desc")
	if not name:
		return None
	doc = frappe.get_doc("Employee Onboarding", name)
	tasks = {t.name: t for t in frappe.get_all("Task", filters={"name": ["in", [a.task for a in doc.activities if a.task] or [""]]}, fields=["name", "status", "exp_end_date"])}
	return {
		"name": doc.name,
		"docstatus": doc.docstatus,
		"status": doc.boarding_status,
		"template": doc.employee_onboarding_template,
		"boarding_begins_on": _s(doc.boarding_begins_on),
		"project": doc.project,
		"activities": [
			{
				"activity_name": a.activity_name,
				"user": a.user,
				"role": a.role,
				"task": a.task,
				"status": tasks[a.task].status if a.task in tasks else None,
				"due": _s(getdate(tasks[a.task].exp_end_date)) if a.task in tasks and tasks[a.task].exp_end_date else None,
				"required": bool(a.required_for_employee_creation),
			}
			for a in doc.activities
		],
	}


def _templates(emp):
	rows = frappe.get_all("Employee Onboarding Template", fields=["name", "title", "company", "department", "designation", "employee_grade"], order_by="title asc")

	def fits(t):
		return all(not t[f] or t[f] == emp[g] for f, g in (("company", "company"), ("department", "department"), ("designation", "designation"), ("employee_grade", "grade")))

	def score(t):
		return sum(1 for f in ("department", "designation", "employee_grade") if t[f])

	return [{"name": t.name, "title": t.title} for t in sorted((t for t in rows if fits(t)), key=score, reverse=True)]


# --- reads --------------------------------------------------------------------------------------


@frappe.whitelist()
def onboarding_list(company=None):
	"""Active employees (newest first) and which of the four parts each has: the hub's "not ready" list.
	`payment` is None for a reader without a payroll role."""
	_portal()._reader()
	payroll = _payroll()
	filters = {"status": "Active"}
	if company:
		filters["company"] = company
	emps = frappe.get_list(
		"Employee",
		filters=filters,
		fields=["name", "employee_name", "company", "department", "designation", "date_of_joining"],
		order_by="date_of_joining desc",
		limit_page_length=MAX_LIST,
	)
	names = [e.name for e in emps] or [""]
	today = nowdate()
	on_pay = set(frappe.get_all("Salary Structure Assignment", filters={"employee": ["in", names], "docstatus": 1}, pluck="employee"))
	on_leave = set(frappe.get_all("Leave Allocation", filters={"employee": ["in", names], "docstatus": 1, "from_date": ["<=", today], "to_date": [">=", today]}, pluck="employee"))
	own_list = set(frappe.get_all("Holiday List Assignment", filters={"applicable_for": "Employee", "assigned_to": ["in", names], "docstatus": 1}, pluck="assigned_to"))
	company_list = {h["company"]: h["holiday_list"] for h in _portal()._company_holidays(sorted({e.company for e in emps}))}
	boarding = {r.employee: r.boarding_status for r in frappe.get_all("Employee Onboarding", filters={"employee": ["in", names], "docstatus": ["!=", 2]}, fields=["employee", "boarding_status"])}
	paid = {}
	if payroll:
		for r in frappe.get_all("Employee", filters={"name": ["in", names]}, fields=["name", *PAYMENT_FIELDS]):
			paid[r.name] = _payment_done(r)
	out = []
	for e in emps:
		row = {
			"employee": e.name,
			"employee_name": e.employee_name,
			"company": e.company,
			"department": e.department,
			"designation": e.designation,
			"date_of_joining": _s(e.date_of_joining),
			"pay": e.name in on_pay,
			"leave": e.name in on_leave,
			"holidays": e.name in own_list or bool(company_list.get(e.company)),
			"payment": paid.get(e.name) if payroll else None,
			"checklist": boarding.get(e.name),
		}
		row["ready"] = row["pay"] and row["leave"] and row["holidays"] and row["payment"] is not False
		out.append(row)
	return out


@frappe.whitelist()
def employee_onboarding(employee):
	"""One employee's four parts, what the grade suggests, and what this reader may do."""
	_portal()._reader()
	emp = _employee(employee)
	payroll = _payroll()
	editor = _portal()._editor()
	payment = _payment(emp, payroll)
	return {
		"employee": {
			"name": emp.name, "employee_name": emp.employee_name, "company": emp.company, "department": emp.department,
			"designation": emp.designation, "grade": emp.grade, "date_of_joining": _s(emp.date_of_joining), "status": emp.status,
			"has_email": bool(_email(emp)),
		},
		"may": {"edit": editor, "pay": editor and payroll, "payment": payroll, "payment_edit": editor and payroll},
		"pay": _pay(emp, payroll),
		"leave": _leave(emp),
		"holidays": _holidays(emp),
		"payment": payment,
		"payment_done": _payment_done(payment) if payroll else None,
		"checklist": _checklist(emp),
		"templates": _templates(emp),
	}


# --- writes -------------------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def onboard(employee, step, values=None):
	"""Do one part for one employee and answer the employee's onboarding again."""
	from folt_customizations import portal_common as _common

	portal = _portal()
	if not portal._editor():
		frappe.throw(_("Onboarding is done by HR Managers. You can read it."), frappe.PermissionError)
	if step not in STEPS:
		frappe.throw(_("Unknown onboarding step {0}.").format(step))
	values = _common.parse_json(values, {}) or {}
	if not isinstance(values, dict):
		frappe.throw(_("The step's values are an object."))
	emp = _employee(employee)
	globals()[f"_do_{step}"](emp, values)
	frappe.clear_messages()
	return employee_onboarding(emp.name)


def _need(values, *fields):
	missing = [f for f in fields if not values.get(f)]
	if missing:
		frappe.throw(_("Missing: {0}.").format(", ".join(missing)))


def _do_pay(emp, values):
	if not _payroll():
		frappe.throw(_("Pay is assigned by an HR Manager with a payroll role."), frappe.PermissionError)
	_need(values, "salary_structure", "from_date")
	structure = values["salary_structure"]
	if not frappe.db.exists("Salary Structure", {"name": structure, "docstatus": 1}):
		frappe.throw(_("{0} is not a submitted salary structure.").format(structure))
	doc = frappe.get_doc({
		"doctype": "Salary Structure Assignment",
		"employee": emp.name,
		"company": emp.company,
		"salary_structure": structure,
		"currency": frappe.db.get_value("Salary Structure", structure, "currency"),
		"from_date": values["from_date"],
		"base": flt(values.get("base")),
		"variable": flt(values.get("variable")),
		"income_tax_slab": values.get("income_tax_slab") or None,
		"grade": emp.grade,
	})
	doc.insert()
	doc.submit()


def _do_leave(emp, values):
	_need(values, "leave_policy")
	if not frappe.db.exists("Leave Policy", {"name": values["leave_policy"], "docstatus": 1}):
		frappe.throw(_("{0} is not a submitted leave policy.").format(values["leave_policy"]))
	period = values.get("leave_period")
	if period:
		start, end = frappe.db.get_value("Leave Period", period, ["from_date", "to_date"]) or (None, None)
		if not start:
			frappe.throw(_("Leave period {0} not found.").format(period))
		# HRMS keeps the period's dates and pro-rates the policy's days for someone joining partway
		# through it (21 days, joining in November of a July-June year: 14).
	else:
		_need(values, "effective_from", "effective_to")
		start, end = values["effective_from"], values["effective_to"]
	doc = frappe.get_doc({
		"doctype": "Leave Policy Assignment",
		"employee": emp.name,
		"company": emp.company,
		"leave_policy": values["leave_policy"],
		"assignment_based_on": "Leave Period" if period else None,
		"leave_period": period or None,
		"effective_from": start,
		"effective_to": end,
		"carry_forward": cint(values.get("carry_forward")),
	})
	doc.insert()
	doc.submit()


def _do_holidays(emp, values):
	_need(values, "holiday_list", "from_date")
	doc = frappe.get_doc({
		"doctype": "Holiday List Assignment",
		"applicable_for": "Employee",
		"assigned_to": emp.name,
		"holiday_list": values["holiday_list"],
		"from_date": values["from_date"],
	})
	doc.insert()
	doc.submit()


def _do_payment(emp, values):
	if not _payroll():
		frappe.throw(_("Salary payment details are for payroll roles."), frappe.PermissionError)
	unknown = set(values) - set(PAYMENT_FIELDS)
	if unknown:
		frappe.throw(_("Unknown payment fields: {0}.").format(", ".join(sorted(unknown))))
	mode = values.get("salary_mode")
	if mode not in SALARY_MODES:
		frappe.throw(_("Choose how salary is paid: Bank, Cash or Cheque."))
	clean = {f: (str(values.get(f) or "").strip() or None) for f in PAYMENT_FIELDS}
	if mode == "Bank":
		_need(clean, "bank_name", "bank_ac_no")
	else:
		clean.update({"bank_name": None, "bank_ac_no": None, "iban": None})
	doc = frappe.get_doc("Employee", emp.name)
	doc.update(clean)
	doc.save()
	# Frappe resets a permlevel field the saver may not write; refuse rather than claim it was saved.
	if (frappe.db.get_value("Employee", emp.name, "salary_mode") or None) != mode:
		frappe.throw(_("Your role may not change salary payment details."), frappe.PermissionError)


def _do_checklist(emp, values):
	from hrms.controllers.employee_boarding_controller import get_onboarding_details

	_need(values, "template")
	if frappe.db.exists("Employee Onboarding", {"employee": emp.name, "docstatus": ["!=", 2]}):
		frappe.throw(_("{0} already has an onboarding checklist.").format(emp.employee_name))
	if not emp.designation:
		frappe.throw(_("Give {0} a designation first: ERPNext's job offer behind the checklist needs one.").format(emp.employee_name))
	template = frappe.get_doc("Employee Onboarding Template", values["template"])
	applicant = emp.job_applicant
	if not applicant:
		email = (values.get("email") or "").strip() or _email(emp)
		if not email:
			frappe.throw(_("{0} has no email. Give one: ERPNext's onboarding starts from a job applicant, who needs an email.").format(emp.employee_name))
		applicant = frappe.get_doc({
			"doctype": "Job Applicant",
			"applicant_name": emp.employee_name,
			"email_id": email,
			"status": "Accepted",
			"designation": emp.designation,
		}).insert().name
		frappe.get_doc("Employee", emp.name).check_permission("write")
		frappe.db.set_value("Employee", emp.name, "job_applicant", applicant, update_modified=False)
	offer = frappe.db.get_value("Job Offer", {"job_applicant": applicant, "status": "Accepted", "docstatus": 1}, "name")
	if not offer:
		offer_doc = frappe.get_doc({
			"doctype": "Job Offer",
			"job_applicant": applicant,
			"applicant_name": emp.employee_name,
			"designation": emp.designation,
			"company": emp.company,
			"offer_date": emp.date_of_joining or nowdate(),
			"status": "Accepted",
		})
		offer_doc.insert()
		offer_doc.submit()
		offer = offer_doc.name
	onboarding = frappe.get_doc({
		"doctype": "Employee Onboarding",
		"job_applicant": applicant,
		"job_offer": offer,
		"employee": emp.name,
		"employee_name": emp.employee_name,
		"date_of_joining": emp.date_of_joining,
		"company": emp.company,
		"department": emp.department,
		"designation": emp.designation,
		"employee_grade": emp.grade,
		"boarding_begins_on": values.get("boarding_begins_on") or nowdate(),
		"employee_onboarding_template": template.name,
		"notify_users_by_email": cint(values.get("notify")),
		"activities": get_onboarding_details(template.name, "Employee Onboarding Template"),
	})
	if not onboarding.activities:
		frappe.throw(_("Template {0} has no activities.").format(template.title or template.name))
	onboarding.insert()
	onboarding.submit()


def _do_activity(emp, values):
	_need(values, "task")
	name = frappe.db.get_value("Employee Onboarding", {"employee": emp.name, "docstatus": 1}, "name")
	if not name:
		frappe.throw(_("{0} has no onboarding checklist started.").format(emp.employee_name))
	onboarding = frappe.get_doc("Employee Onboarding", name)
	onboarding.check_permission("write")
	if values["task"] not in {a.task for a in onboarding.activities if a.task}:
		frappe.throw(_("That task is not on {0}'s checklist.").format(emp.employee_name))
	task = frappe.get_doc("Task", values["task"])
	done = bool(values.get("done"))
	task.status = "Completed" if done else "Open"
	if done:
		task.completed_by = frappe.session.user
		task.completed_on = nowdate()
	else:
		task.completed_by = task.completed_on = None
	task.save()
