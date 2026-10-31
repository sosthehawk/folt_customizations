"""The HR portal's server half: payroll runs, payslip PDFs and the HR overview.

The Go BFF (folt-workflow-ui) calls these AS THE USER, like bff_support. They exist because each is
either several HRMS steps that must happen in one transaction (a payroll run), something
/api/resource cannot express (a PDF, a headcount by status), or an HRMS step the FoLT workflow
skips (posting a run to the accounts).

  payroll_runs()          The Payroll Entries, newest first, each with its slips by workflow state.
  payroll_preview(month)  Who a run for that month would pay, and what would stop it, WITHOUT
                          saving anything: no salary structure, no holiday list, already paid,
                          a payable account of the wrong type.
  start_payroll(month)    Creates the month's Payroll Entry and fills its employees (HRMS's own
                          fill_employee_details). Refuses a second live run for the same period.
  create_salary_slips(name)
                          Submits the entry, which is how HRMS creates the draft slips. The slips
                          then go through FoLT Payroll Approval one by one (Finance Assistant
                          submits, Executive Director approves) on the document page.
  post_payroll(name)      Books the run: HRMS's own accrual journal entry over the APPROVED slips,
                          once none is still in Draft or Pending. FoLT's workflow submits slips one
                          at a time, which never reaches HRMS's bulk-submit step -- the only place
                          make_accrual_jv_entry is called -- so without this an approved payroll
                          never reaches the ledger.
  cancel_payroll(name)    Discards a draft run, or cancels a submitted one while none of its slips
                          has been approved (HRMS's cancel would cancel and DELETE approved slips).
  payroll_run(name)       One run: its employees, its slips and what is left to do.
  payslip_pdf(name)       One slip as a PDF in the FoLT Salary Slip format, as Frappe renders it.
  payslips_pdf(names)     Several slips in one PDF, for payroll staff. At most MAX_BULK_PDF.
  hr_overview()           The HR dashboard's figures for HR staff, from Frappe's own lists.

Every permission question is asked with frappe.has_permission before anything that could throw
(bff_support's "ask, never catch"). Payroll writes need Payroll Entry permission, which on this site
is HR Manager's alone. A PDF needs read on each slip, and salary_slip_access scopes that read to the
slip's own employee for everyone without a payroll role.

THE CONTRACT IS EXTERNAL: docs/bff-upstream-map.md section 4 in folt-workflow-ui fixes each
function's arguments and return shape, and the BFF decodes these dicts field for field.
"""

import io
import json
import re

import frappe
from frappe import _
from frappe.utils import add_days, add_months, cint, flt, get_first_day, get_last_day, getdate, nowdate

PAYROLL_ENTRY = "Payroll Entry"
SALARY_SLIP = "Salary Slip"
SLIP_FORMAT = "FoLT Salary Slip"
MAX_BULK_PDF = 200
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


# --- helpers -------------------------------------------------------------------------------


def _require(doctype, ptype, doc=None):
	if not frappe.has_permission(doctype, ptype, doc=doc):
		frappe.throw(_("You are not allowed to do this."), frappe.PermissionError)


def _company():
	company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)
	if not company:
		frappe.throw(_("No default company is set."))
	return company


def _month_bounds(month):
	if not month or not MONTH.match(month):
		frappe.throw(_("The month must be given as YYYY-MM."))
	first = getdate(month + "-01")
	return first, get_last_day(first)


def _entry_defaults(company):
	"""The accounts a run books against, as the October run (seed_hr_payroll) set them."""
	values = frappe.db.get_value(
		"Company",
		company,
		["default_payroll_payable_account", "cost_center", "default_currency", "default_bank_account", "default_cash_account"],
		as_dict=True,
	)
	from folt_customizations.payroll_accounts import payment_account

	return {
		"payroll_payable_account": values.default_payroll_payable_account,
		"cost_center": values.cost_center,
		"currency": values.default_currency,
		# The Payroll accounts setting, else the company's bank, else its cash account.
		"payment_account": payment_account(company),
	}


def _live_entry(company, start, end):
	return frappe.db.get_value(
		PAYROLL_ENTRY, {"company": company, "start_date": start, "end_date": end, "docstatus": ["<", 2]}
	)


def _slips_of(entry):
	return frappe.get_all(
		SALARY_SLIP,
		filters={"payroll_entry": entry},
		fields=[
			"name",
			"employee",
			"employee_name",
			"department",
			"workflow_state",
			"docstatus",
			"gross_pay",
			"total_deduction",
			"net_pay",
			"currency",
			"journal_entry",
		],
		order_by="employee_name asc",
	)


def _state(slip):
	return slip.workflow_state or {0: "Draft", 1: "Submitted", 2: "Cancelled"}[slip.docstatus]


def _stage(entry, slips):
	"""Where a run stands, as one word the portal leads with."""
	if entry.docstatus == 2:
		return "cancelled"
	if entry.docstatus == 0:
		return "draft"
	if not slips:
		return "creating"
	if entry.salary_slips_submitted:
		return "posted"
	if any(s.docstatus == 0 and _state(s) != "Rejected" for s in slips):
		return "approving"
	return "ready_to_post"


def _summary(entry, slips):
	by_state = {}
	for s in slips:
		row = by_state.setdefault(_state(s), {"state": _state(s), "count": 0, "gross": 0.0, "net": 0.0})
		row["count"] += 1
		row["gross"] += flt(s.gross_pay)
		row["net"] += flt(s.net_pay)
	approved = [s for s in slips if s.docstatus == 1]
	return {
		"name": entry.name,
		"company": entry.company,
		"start_date": str(entry.start_date),
		"end_date": str(entry.end_date),
		"posting_date": str(entry.posting_date),
		"currency": entry.currency,
		"docstatus": entry.docstatus,
		"stage": _stage(entry, slips),
		"number_of_employees": cint(entry.number_of_employees),
		"slips": len(slips),
		"approved": len(approved),
		"gross": flt(sum(flt(s.gross_pay) for s in slips), 2),
		"deductions": flt(sum(flt(s.total_deduction) for s in slips), 2),
		"net": flt(sum(flt(s.net_pay) for s in slips), 2),
		"by_state": sorted(
			({**r, "gross": flt(r["gross"], 2), "net": flt(r["net"], 2)} for r in by_state.values()),
			key=lambda r: r["state"],
		),
		"journal_entry": next((s.journal_entry for s in approved if s.journal_entry), None),
		"modified": str(entry.modified),
	}


# --- payroll runs --------------------------------------------------------------------------


@frappe.whitelist(methods=["GET"])
def payroll_runs():
	_require(PAYROLL_ENTRY, "read")
	entries = frappe.get_list(
		PAYROLL_ENTRY,
		fields=[
			"name",
			"company",
			"start_date",
			"end_date",
			"posting_date",
			"currency",
			"docstatus",
			"number_of_employees",
			"salary_slips_submitted",
			"modified",
		],
		order_by="start_date desc, creation desc",
		limit_page_length=24,
	)
	return [_summary(e, _slips_of(e.name)) for e in entries]


@frappe.whitelist(methods=["GET"])
def payroll_preview(month):
	"""Who a run for `month` would pay, and the reasons anyone active would be left out."""
	_require(PAYROLL_ENTRY, "create")
	from hrms.payroll.doctype.payroll_entry.payroll_entry import get_employee_list
	from hrms.utils.holiday_list import get_holiday_list_for_employee

	company = _company()
	start, end = _month_bounds(month)
	defaults = _entry_defaults(company)
	filters = frappe._dict(
		company=company,
		currency=defaults["currency"],
		start_date=start,
		end_date=end,
		payroll_payable_account=defaults["payroll_payable_account"],
		salary_slip_based_on_timesheet=0,
		payroll_frequency="Monthly",
	)
	eligible = get_employee_list(filters=filters, as_dict=True, ignore_match_conditions=True)
	eligible_ids = {e.employee for e in eligible}

	active = frappe.get_all(
		"Employee",
		filters={"company": company, "status": "Active"},
		fields=["name", "employee_name", "department", "designation", "date_of_joining"],
		order_by="employee_name asc",
	)
	paid = set(
		frappe.get_all(
			SALARY_SLIP,
			filters={"company": company, "start_date": start, "end_date": end, "docstatus": ["<", 2]},
			pluck="employee",
		)
	)

	exceptions = []
	for emp in active:
		if emp.name in eligible_ids:
			if not get_holiday_list_for_employee(emp.name, raise_exception=False, as_on=start):
				exceptions.append(
					{
						"employee": emp.name,
						"employee_name": emp.employee_name,
						"reason": "no_holiday_list",
						"message": _("No holiday list is assigned, so the slip cannot count working days."),
					}
				)
			continue
		if emp.name in paid:
			reason, message = "already_paid", _("Already has a salary slip for this month.")
		elif emp.date_of_joining and getdate(emp.date_of_joining) > end:
			reason, message = "not_joined", _("Joins after the month ends.")
		else:
			reason, message = (
				"no_salary_structure",
				_("No submitted salary structure assignment from this month or earlier."),
			)
		exceptions.append(
			{"employee": emp.name, "employee_name": emp.employee_name, "reason": reason, "message": message}
		)

	blockers = []
	if not defaults["payroll_payable_account"]:
		blockers.append(_("The company has no default payroll payable account."))
	elif frappe.db.get_value("Account", defaults["payroll_payable_account"], "account_type") != "Payable":
		blockers.append(
			_("The payroll payable account {0} must have account type Payable.").format(
				defaults["payroll_payable_account"]
			)
		)
	if not defaults["payment_account"]:
		blockers.append(_("The company has no bank or cash account to pay salaries from."))
	existing = _live_entry(company, start, end)
	if existing:
		blockers.append(_("A payroll run for this month already exists: {0}.").format(existing))
	if not eligible:
		blockers.append(_("Nobody is eligible for a run this month."))

	names = {e.name: e for e in active}
	return {
		"month": month,
		"company": company,
		"start_date": str(start),
		"end_date": str(end),
		"currency": defaults["currency"],
		"existing": existing,
		"employees": [
			{
				"employee": e.employee,
				"employee_name": e.employee_name,
				"department": e.department,
				"designation": e.designation,
			}
			for e in sorted(eligible, key=lambda e: (e.employee_name or "").lower())
		],
		"exceptions": [x for x in exceptions if x["employee"] in names],
		"blockers": blockers,
	}


@frappe.whitelist(methods=["POST"])
def start_payroll(month):
	_require(PAYROLL_ENTRY, "create")
	company = _company()
	start, end = _month_bounds(month)
	existing = _live_entry(company, start, end)
	if existing:
		frappe.throw(_("A payroll run for this month already exists: {0}.").format(existing), frappe.DuplicateEntryError)
	defaults = _entry_defaults(company)
	entry = frappe.get_doc(
		{
			"doctype": PAYROLL_ENTRY,
			"company": company,
			"posting_date": min(getdate(nowdate()), end) if getdate(nowdate()) >= start else end,
			"start_date": start,
			"end_date": end,
			"payroll_frequency": "Monthly",
			"exchange_rate": 1,
			**defaults,
		}
	)
	entry.insert()
	entry.fill_employee_details()
	entry.save()
	return payroll_run(entry.name)


@frappe.whitelist(methods=["POST"])
def create_salary_slips(name):
	entry = frappe.get_doc(PAYROLL_ENTRY, name)
	_require(PAYROLL_ENTRY, "submit", entry)
	if entry.docstatus != 0:
		frappe.throw(_("The slips of this run have already been created."))
	entry.submit()
	return payroll_run(entry.name)


@frappe.whitelist(methods=["POST"])
def post_payroll(name):
	entry = frappe.get_doc(PAYROLL_ENTRY, name)
	_require(PAYROLL_ENTRY, "write", entry)
	slips = _slips_of(name)
	stage = _stage(entry, slips)
	if stage == "posted":
		frappe.throw(_("This run is already posted to the accounts."))
	if stage != "ready_to_post":
		frappe.throw(_("Every slip must be approved or rejected before the run is posted."))
	approved = [frappe.get_doc(SALARY_SLIP, s.name) for s in slips if s.docstatus == 1]
	if not approved:
		frappe.throw(_("No slip of this run is approved, so there is nothing to post."))
	entry.make_accrual_jv_entry(approved)
	entry.db_set({"salary_slips_submitted": 1, "status": "Submitted", "error_message": ""})
	return payroll_run(entry.name)


@frappe.whitelist(methods=["POST"])
def cancel_payroll(name):
	entry = frappe.get_doc(PAYROLL_ENTRY, name)
	if entry.docstatus == 0:
		_require(PAYROLL_ENTRY, "delete", entry)
		frappe.delete_doc(PAYROLL_ENTRY, name)
		return {"name": name, "deleted": True}
	_require(PAYROLL_ENTRY, "cancel", entry)
	if entry.docstatus == 2:
		frappe.throw(_("This run is already cancelled."))
	if any(s.docstatus == 1 for s in _slips_of(name)):
		frappe.throw(
			_("Some slips of this run are approved. A run with approved slips cannot be cancelled here.")
		)
	entry.cancel()
	return {"name": name, "deleted": False}


@frappe.whitelist(methods=["GET"])
def payroll_run(name):
	entry = frappe.get_doc(PAYROLL_ENTRY, name)
	_require(PAYROLL_ENTRY, "read", entry)
	slips = _slips_of(name)
	slipped = {s.employee for s in slips}
	out = _summary(entry, slips)
	out["employees"] = [
		{
			"employee": e.employee,
			"employee_name": e.employee_name,
			"department": e.department,
			"designation": e.designation,
			"has_slip": e.employee in slipped,
		}
		for e in entry.employees
	]
	out["slip_rows"] = [
		{
			"name": s.name,
			"employee": s.employee,
			"employee_name": s.employee_name,
			"department": s.department,
			"state": _state(s),
			"docstatus": s.docstatus,
			"currency": s.currency,
			"gross_pay": flt(s.gross_pay, 2),
			"total_deduction": flt(s.total_deduction, 2),
			"net_pay": flt(s.net_pay, 2),
		}
		for s in slips
	]
	out["error_message"] = entry.error_message or None
	return out


# --- payslip PDFs --------------------------------------------------------------------------


def _send_pdf(filename, content):
	frappe.local.response.filename = filename
	frappe.local.response.filecontent = content
	frappe.local.response.type = "pdf"


def _slip_pdf(name, output=None):
	return frappe.get_print(SALARY_SLIP, name, SLIP_FORMAT, as_pdf=True, output=output)


@frappe.whitelist(methods=["GET"])
def payslip_pdf(name):
	"""One slip, if the caller may read it. salary_slip_access makes that 'their own' for anyone
	without a payroll role; a draft is refused to them too, as the payslips list shows only
	submitted ones."""
	if not frappe.db.exists(SALARY_SLIP, name) or not frappe.has_permission(SALARY_SLIP, "read", doc=name):
		frappe.throw(_("That payslip does not exist, or it is not yours."), frappe.DoesNotExistError)
	from folt_customizations.salary_slip_access import is_payroll_user

	if not is_payroll_user() and frappe.db.get_value(SALARY_SLIP, name, "docstatus") != 1:
		frappe.throw(_("That payslip does not exist, or it is not yours."), frappe.DoesNotExistError)
	_send_pdf(name.replace("/", "-") + ".pdf", _slip_pdf(name))


@frappe.whitelist(methods=["GET"])
def payslips_pdf(names=None, payroll_entry=None):
	"""Several slips in one PDF, for payroll staff: the listed names, or every slip of a run."""
	from pypdf import PdfWriter

	from folt_customizations.salary_slip_access import is_payroll_user

	if not is_payroll_user():
		frappe.throw(_("Payroll documents are only available to payroll roles."), frappe.PermissionError)
	if payroll_entry:
		# By the slips' own link, not the entry: Payroll Entry is HR Manager's alone, while every
		# payroll role reads the slips.
		names = frappe.get_list(
			SALARY_SLIP,
			filters={"payroll_entry": payroll_entry, "docstatus": ["<", 2]},
			pluck="name",
			order_by="employee_name asc",
			limit_page_length=0,
		)
	elif isinstance(names, str):
		names = json.loads(names)
	names = list(dict.fromkeys(names or []))
	if not names:
		frappe.throw(_("Choose at least one salary slip."))
	if len(names) > MAX_BULK_PDF:
		frappe.throw(_("At most {0} slips fit in one download.").format(MAX_BULK_PDF))
	for name in names:
		if not frappe.db.exists(SALARY_SLIP, name) or not frappe.has_permission(SALARY_SLIP, "read", doc=name):
			frappe.throw(_("Salary slip {0} is not available to you.").format(name), frappe.PermissionError)
	writer = PdfWriter()
	for name in names:
		_slip_pdf(name, output=writer)
	buf = io.BytesIO()
	writer.write(buf)
	_send_pdf(f"salary-slips-{payroll_entry or len(names)}.pdf".replace("/", "-"), buf.getvalue())


# --- HR overview ---------------------------------------------------------------------------


def _upcoming(rows, field, today, days):
	"""Rows whose `field` (a date) falls on a day-of-year in the next `days` days, soonest first,
	with that next date and the years it completes."""
	out = []
	for r in rows:
		when = r.get(field)
		if not when:
			continue
		when = getdate(when)
		for year in (today.year, today.year + 1):
			try:
				nxt = when.replace(year=year)
			except ValueError:  # 29 February in a common year
				nxt = when.replace(year=year, day=28)
			if today <= nxt <= add_days(today, days):
				out.append({**r, "next": str(nxt), "years": nxt.year - when.year})
				break
	return sorted(out, key=lambda r: r["next"])


@frappe.whitelist(methods=["GET"])
def hr_overview():
	"""The HR dashboard's figures, from lists Frappe filters by the caller's own permissions."""
	_require("Employee", "read")
	from folt_customizations.salary_slip_access import is_payroll_user

	today = getdate(nowdate())
	employees = frappe.get_list(
		"Employee",
		fields=[
			"name",
			"employee_name",
			"status",
			"department",
			"designation",
			"date_of_birth",
			"date_of_joining",
			"relieving_date",
			"contract_end_date",
			"final_confirmation_date",
			"image",
		],
		limit_page_length=0,
	)
	active = [e for e in employees if e.status == "Active"]
	by_status = {}
	for e in employees:
		by_status[e.status] = by_status.get(e.status, 0) + 1
	by_department = {}
	for e in active:
		key = e.department or ""
		by_department[key] = by_department.get(key, 0) + 1

	on_leave = frappe.get_list(
		"Leave Application",
		filters={"status": "Approved", "docstatus": 1, "from_date": ["<=", today], "to_date": [">=", today]},
		fields=["employee", "employee_name", "leave_type", "from_date", "to_date"],
		limit_page_length=0,
	)
	pending_leave = frappe.db.count("Leave Application", {"status": "Open", "docstatus": 0}) if frappe.has_permission(
		"Leave Application", "read"
	) else 0
	marked_today = frappe.get_list(
		"Attendance",
		filters={"attendance_date": today, "docstatus": 1},
		fields=["status"],
		limit_page_length=0,
	)
	present = sum(1 for a in marked_today if a.status in ("Present", "Work From Home"))

	people = [
		{"employee": e.name, "employee_name": e.employee_name, "image": e.image, "date_of_birth": e.date_of_birth, "date_of_joining": e.date_of_joining}
		for e in active
	]
	birthdays = [
		{"employee": r["employee"], "employee_name": r["employee_name"], "image": r["image"], "date": r["next"]}
		for r in _upcoming(people, "date_of_birth", today, 30)
	]
	anniversaries = [
		{"employee": r["employee"], "employee_name": r["employee_name"], "image": r["image"], "date": r["next"], "years": r["years"]}
		for r in _upcoming(people, "date_of_joining", today, 30)
		if r["years"] > 0
	]
	horizon = add_days(today, 60)
	contracts = sorted(
		(
			{"employee": e.name, "employee_name": e.employee_name, "date": str(e.contract_end_date)}
			for e in active
			if e.contract_end_date and today <= getdate(e.contract_end_date) <= horizon
		),
		key=lambda r: r["date"],
	)
	confirmations = sorted(
		(
			{"employee": e.name, "employee_name": e.employee_name, "date": str(e.final_confirmation_date)}
			for e in active
			if e.final_confirmation_date and today <= getdate(e.final_confirmation_date) <= horizon
		),
		key=lambda r: r["date"],
	)
	joined = sorted(
		(
			{"employee": e.name, "employee_name": e.employee_name, "date": str(e.date_of_joining)}
			for e in active
			if e.date_of_joining and getdate(e.date_of_joining) >= add_months(today, -3)
		),
		key=lambda r: r["date"],
		reverse=True,
	)

	payroll = None
	if is_payroll_user() and frappe.has_permission(SALARY_SLIP, "read"):
		first, last = get_first_day(today), get_last_day(today)
		slips = frappe.get_list(
			SALARY_SLIP,
			filters={"start_date": ["<=", last], "end_date": [">=", first], "docstatus": ["<", 2]},
			fields=["workflow_state", "docstatus", "gross_pay", "net_pay"],
			limit_page_length=0,
		)
		latest = None
		if frappe.has_permission(PAYROLL_ENTRY, "read"):
			entry = frappe.get_list(PAYROLL_ENTRY, fields=["name"], order_by="start_date desc, creation desc", limit_page_length=1)
			if entry:
				e = frappe.get_doc(PAYROLL_ENTRY, entry[0].name)
				latest = _summary(e, _slips_of(e.name))
		payroll = {
			"month": str(first)[:7],
			"slips": len(slips),
			"approved": sum(1 for s in slips if s.docstatus == 1),
			"waiting": sum(1 for s in slips if s.docstatus == 0 and s.workflow_state != "Rejected"),
			"gross": flt(sum(flt(s.gross_pay) for s in slips), 2),
			"net": flt(sum(flt(s.net_pay) for s in slips), 2),
			"latest_run": latest,
		}

	return {
		"today": str(today),
		"headcount": {
			"total": len(employees),
			"active": len(active),
			"by_status": [{"status": k, "count": v} for k, v in sorted(by_status.items())],
			"by_department": [
				{"department": k or None, "count": v} for k, v in sorted(by_department.items(), key=lambda kv: (-kv[1], kv[0]))
			],
		},
		"attendance_today": {"marked": len(marked_today), "present": present},
		"on_leave_today": [
			{
				"employee": a.employee,
				"employee_name": a.employee_name,
				"leave_type": a.leave_type,
				"from_date": str(a.from_date),
				"to_date": str(a.to_date),
			}
			for a in on_leave
		],
		"pending_leave": pending_leave,
		"birthdays": birthdays,
		"anniversaries": anniversaries,
		"contract_endings": contracts,
		"confirmations_due": confirmations,
		"recent_joiners": joined[:10],
		"payroll": payroll,
	}


# --- employee logins -----------------------------------------------------------------------
#
# Leave and payslips are an employee's own only once their login is linked to their Employee
# record (Employee.user_id): that link is how HRMS, salary_slip_access and the BFF find "me". HR
# roles may edit Employee but may not create Users, so these three are the narrow way through:
# a NEW login gets the Employee role and nothing else, and an existing login is only linked
# (ERPNext's Employee.update_user then adds the Employee role). No other role is ever granted here.

HR_ROLES = {"HR User", "HR Manager"}


def _require_hr_on(employee):
	if frappe.session.user != "Administrator" and not HR_ROLES.intersection(frappe.get_roles()):
		frappe.throw(_("Only HR can give an employee a login."), frappe.PermissionError)
	emp = frappe.get_doc("Employee", employee)
	_require("Employee", "write", emp)
	if emp.user_id:
		frappe.throw(_("{0} already has a login: {1}.").format(emp.employee_name, emp.user_id))
	return emp


@frappe.whitelist(methods=["GET"])
def employee_login_choices():
	"""Enabled desk logins not yet linked to any employee (for "link an existing login")."""
	if frappe.session.user != "Administrator" and not HR_ROLES.intersection(frappe.get_roles()):
		frappe.throw(_("Only HR can give an employee a login."), frappe.PermissionError)
	linked = set(frappe.get_all("Employee", filters={"user_id": ["is", "set"]}, pluck="user_id"))
	users = frappe.get_all(
		"User",
		filters={"enabled": 1, "user_type": "System User", "name": ["not in", ["Administrator", "Guest"]]},
		fields=["name", "full_name"],
		order_by="full_name asc",
	)
	return [{"value": u.name, "label": f"{u.full_name or u.name} ({u.name})"} for u in users if u.name not in linked]


@frappe.whitelist(methods=["POST"])
def create_employee_login(employee, email):
	"""A new login for the employee, holding the Employee role only, with Frappe's welcome e-mail
	(a link to set a password). The e-mail must not belong to an existing login."""
	from frappe.utils import validate_email_address

	emp = _require_hr_on(employee)
	email = validate_email_address((email or "").strip(), True)
	if frappe.db.exists("User", email):
		frappe.throw(_("A login with {0} already exists. Link it instead.").format(email), frappe.DuplicateEntryError)
	parts = (emp.employee_name or emp.first_name).split(" ")
	user = frappe.new_doc("User")
	user.update(
		{
			"email": email,
			"enabled": 1,
			"user_type": "System User",
			"first_name": parts[0],
			"last_name": " ".join(parts[1:]),
			"gender": emp.gender,
			"birth_date": emp.date_of_birth,
			"send_welcome_email": 1,
		}
	)
	user.append_roles("Employee")
	user.flags.ignore_permissions = True
	user.insert()
	_link(emp, user.name)
	return {"employee": emp.name, "user": user.name, "created": True}


@frappe.whitelist(methods=["POST"])
def link_employee_login(employee, user):
	"""Link an existing, enabled desk login that no other employee holds."""
	emp = _require_hr_on(employee)
	if not frappe.db.get_value("User", {"name": user, "enabled": 1, "user_type": "System User"}) or user in ("Administrator", "Guest"):
		frappe.throw(_("That login does not exist or cannot be linked."), frappe.DoesNotExistError)
	other = frappe.db.get_value("Employee", {"user_id": user}, "employee_name")
	if other:
		frappe.throw(_("{0} is already linked to {1}.").format(user, other))
	_link(emp, user)
	return {"employee": emp.name, "user": user, "created": False}


def _link(emp, user):
	"""Set the login and save as the caller (Employee write). create_user_permission is switched
	off: with it on, ERPNext's save also creates a User Permission pinning the user to this
	Employee, which HR roles may not create -- and which FoLT does not rely on, since
	salary_slip_access scopes payslips and the BFF scopes the rest. Same as the requester's record."""
	emp.user_id = user
	emp.create_user_permission = 0
	emp.save()
