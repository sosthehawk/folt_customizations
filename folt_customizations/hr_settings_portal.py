"""HR settings without the Desk (ADR-009): the allow-listed edge the FoLT app's HR > Settings area
reads and writes through, run as the user.

The same shape as procurement_portal and finance_portal, on portal_common's document half: this
module decides WHAT is part of HR settings and WHO may come in; Frappe's permissions and the
doctypes' own controllers decide everything else.

Who (the user's choice, 2026-11-03): HR Manager (and System Manager) change HR settings, HR User
reads them. That rule is Frappe's own too -- permissions.HR_READ_ONLY takes HR User's writes away
on every migrate -- and it is asked again here, so a write from HR User is refused at the door even
on a site where an administrator gave a permission back.

DOCTYPES grows by phase: phase 1 is the general settings and the organisation lists HR keeps;
holidays (phase 2) are the holiday lists and who each applies to; leave (phase 3) is leave types,
periods, policies, their assignment and allocations, with the Leave Control Panel's bulk allocation;
pay set-up (phase 4) is salary components and structures (with a slip preview), payroll periods,
income tax slabs and shift types; onboarding comes after.

KEPT WITH THE APP. FoLT's statutory salary components and its salary structure are fixtures
(hooks.FOLT_SALARY_COMPONENTS, FOLT_SALARY_STRUCTURES), and `bench migrate` re-imports a fixture
over the record every time -- an edit made here would be reverted, silently, on the next deploy
(verified 2026-11-03: a changed description came back blank and the accounts table emptied). So they
are shown here in full and refused for any write; their rates change with a release, where
payroll_e2e's known-answer checks guard them. Anything HR adds is theirs to change.
"""

import frappe
from frappe import _

from folt_customizations import portal_common as _common

READERS = ("HR User", "HR Manager", "System Manager")
EDITORS = ("HR Manager", "System Manager")

# doctype -> (group, list columns, searched fields). Columns a doctype's meta does not have on this
# version are dropped at run time (portal_common.columns).
DOCTYPES = {
	# general (phase 1)
	"HR Settings": ("general", [], []),
	"Payroll Settings": ("general", [], []),
	"Department": ("organisation", ["department_name", "parent_department", "company", "is_group", "disabled"], ["name", "department_name"]),
	"Designation": ("organisation", ["designation_name"], ["name", "designation_name"]),
	"Employment Type": ("organisation", ["employee_type_name"], ["name", "employee_type_name"]),
	"Branch": ("organisation", ["branch"], ["name", "branch"]),
	"Employee Grade": ("organisation", ["default_salary_structure", "currency", "default_base_pay"], ["name"]),
	# holidays (phase 2)
	"Holiday List": ("holidays", ["holiday_list_name", "from_date", "to_date", "weekly_off", "total_holidays"], ["name", "holiday_list_name"]),
	"Holiday List Assignment": ("holidays", ["applicable_for", "assigned_to", "employee_name", "holiday_list", "from_date"], ["name", "assigned_to", "employee_name", "holiday_list"]),
	# leave (phase 3)
	"Leave Type": ("leave", ["max_leaves_allowed", "is_lwp", "is_carry_forward", "include_holiday", "is_earned_leave", "allow_encashment"], ["name", "leave_type_name"]),
	"Leave Period": ("leave", ["from_date", "to_date", "company", "is_active"], ["name"]),
	"Leave Policy": ("leave", ["title"], ["name", "title"]),
	"Leave Policy Assignment": ("leave", ["employee", "employee_name", "leave_policy", "effective_from", "effective_to", "leaves_allocated"], ["name", "employee", "employee_name", "leave_policy"]),
	"Leave Allocation": ("leave", ["employee", "employee_name", "leave_type", "from_date", "to_date", "total_leaves_allocated", "leave_policy"], ["name", "employee", "employee_name", "leave_type"]),
	# pay set-up (phase 4)
	"Salary Component": ("pay", ["salary_component_abbr", "type", "depends_on_payment_days", "is_tax_applicable", "statistical_component", "disabled"], ["name", "salary_component_abbr"]),
	"Salary Structure": ("pay", ["company", "payroll_frequency", "is_active", "currency"], ["name"]),
	"Payroll Period": ("pay", ["company", "start_date", "end_date"], ["name"]),
	"Income Tax Slab": ("pay", ["company", "effective_from", "currency", "disabled"], ["name"]),
	"Shift Type": ("pay", ["start_time", "end_time", "enable_auto_attendance", "holiday_list"], ["name"]),
}


def kept_with_app():
	"""{doctype: [names]} that are fixtures of this app: read here, never written."""
	from folt_customizations import hooks

	return {
		"Salary Component": list(getattr(hooks, "FOLT_SALARY_COMPONENTS", [])),
		"Salary Structure": list(getattr(hooks, "FOLT_SALARY_STRUCTURES", [])),
	}


def _not_kept(doctype, name):
	if name and name in kept_with_app().get(doctype, []):
		frappe.throw(
			_("{0} is kept with the FoLT app: its rates change with a release, so an edit here would be undone on the next deploy.").format(name),
			frappe.PermissionError,
			title=_("Kept with the app"),
		)

# How many days ahead the hub warns that a company's holiday list is running out.
HOLIDAY_LIST_WARN_DAYS = 60

# The setters a controller's own validate() runs, for a preview of an unsaved form. Nothing in
# phase 1 computes anything, so every doctype gets ERPNext's set_missing_values if it has one.
_PREVIEW = {}


def _roles():
	return set(frappe.get_roles())


def _reader():
	if frappe.session.user != "Administrator" and not _roles() & set(READERS):
		frappe.throw(_("HR settings are open to HR Users, HR Managers and System Managers."), frappe.PermissionError)


def _editor() -> bool:
	return frappe.session.user == "Administrator" or bool(_roles() & set(EDITORS))


def _doctype(doctype: str) -> str:
	if doctype not in DOCTYPES:
		frappe.throw(_("{0} is not part of HR settings.").format(doctype), frappe.PermissionError)
	_reader()
	return doctype


def _writable(doctype: str) -> str:
	_doctype(doctype)
	if not _editor():
		frappe.throw(_("HR settings are changed by HR Managers. You can read them."), frappe.PermissionError)
	return doctype


def _may(doctype, ptype="read"):
	try:
		return bool(frappe.has_permission(doctype, ptype))
	except Exception:  # noqa: BLE001 -- a doctype missing on this version is simply not offered
		return False


def _count(doctype):
	if frappe.get_meta(doctype).issingle or not _may(doctype):
		return None
	return frappe.db.count(doctype) if frappe.has_permission(doctype, "read") else None


# --- context ----------------------------------------------------------------------------------


@frappe.whitelist()
def context():
	"""What every HR settings screen needs to know first: what the reader may do with each doctype,
	and how many of each there are (the hub's figures)."""
	_reader()
	editor = _editor()
	capabilities, counts = {}, {}
	for doctype in DOCTYPES:
		if not frappe.db.exists("DocType", doctype):
			continue
		caps = {ptype: _may(doctype, ptype) for ptype in ("read", "create", "write", "submit", "cancel", "delete")}
		if not editor:
			caps.update({"create": False, "write": False, "submit": False, "cancel": False, "delete": False})
		capabilities[doctype] = caps
		counts[doctype] = _count(doctype)
	companies = frappe.get_all("Company", fields=["name", "abbr", "default_currency"], order_by="name")
	default_company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value("Global Defaults", "default_company")
	return {
		"user": frappe.session.user,
		"companies": companies,
		"default_company": default_company if default_company in {c.name for c in companies} else (companies[0].name if companies else None),
		"capabilities": capabilities,
		"counts": counts,
		"workflows": {},
		"roles": {"editor": editor},
		"holidays": _company_holidays([c.name for c in companies]),
		"leave_periods": _company_leave_periods([c.name for c in companies]),
		"kept_with_app": kept_with_app(),
	}


def _company_leave_periods(companies):
	"""Each company's active leave period covering today, if any: what HRMS's leave allocation and
	the Leave Control Panel take their dates from."""
	from frappe.utils import nowdate

	today = nowdate()
	out = []
	for company in companies:
		row = frappe.get_all(
			"Leave Period",
			filters={"company": company, "is_active": 1, "from_date": ["<=", today], "to_date": [">=", today]},
			fields=["name", "from_date", "to_date"],
			order_by="from_date desc",
			limit=1,
		)
		out.append(
			{"company": company, "leave_period": row[0].name if row else None,
			 "from_date": str(row[0].from_date) if row else None, "to_date": str(row[0].to_date) if row else None}
		)
	return out


def _company_holidays(companies):
	"""Each company's holiday list today: the latest submitted Holiday List Assignment for the company
	that has started -- what HRMS 16 itself reads (hrms.utils.holiday_list), not the Company's
	default_holiday_list -- and how many days it has left, so the hub can warn before it runs out."""
	from frappe.utils import date_diff, getdate, nowdate

	today = getdate(nowdate())
	out = []
	for company in companies:
		row = frappe.get_all(
			"Holiday List Assignment",
			filters={"applicable_for": "Company", "assigned_to": company, "docstatus": 1, "from_date": ["<=", today]},
			fields=["holiday_list", "from_date"],
			order_by="from_date desc",
			limit=1,
		)
		if not row:
			out.append({"company": company, "holiday_list": None, "from_date": None, "to_date": None, "days_left": None, "next": None})
			continue
		to_date = frappe.db.get_value("Holiday List", row[0].holiday_list, "to_date")
		upcoming = frappe.get_all(
			"Holiday List Assignment",
			filters={"applicable_for": "Company", "assigned_to": company, "docstatus": 1, "from_date": [">", today]},
			fields=["holiday_list", "from_date"],
			order_by="from_date asc",
			limit=1,
		)
		out.append(
			{
				"company": company,
				"holiday_list": row[0].holiday_list,
				"from_date": str(row[0].from_date),
				"to_date": str(to_date) if to_date else None,
				"days_left": date_diff(to_date, today) if to_date else None,
				"next": {"holiday_list": upcoming[0].holiday_list, "from_date": str(upcoming[0].from_date)} if upcoming else None,
			}
		)
	return out


# --- lists and records ------------------------------------------------------------------------


@frappe.whitelist()
def records(doctype, q=None, filters=None, order_by=None, start=0, page_length=20):
	"""One page of an HR settings list, as the reader may see it, with its total."""
	_doctype(doctype)
	_group, wanted, search = DOCTYPES[doctype]
	return _common.list_view(doctype, wanted, search, q, filters, order_by, start, page_length)


@frappe.whitelist()
def record(doctype, name=None):
	"""A settings record with its history and what the reader may do with it."""
	_doctype(doctype)
	out = _common.record_view(doctype, name)
	if not _editor() or out["name"] in kept_with_app().get(doctype, []):
		out["permissions"].update({"write": False, "create": False, "submit": False, "cancel": False, "amend": False, "delete": False})
	return out


@frappe.whitelist()
def meta(doctype):
	"""The fields of an HR settings doctype (and its child tables), for a form drawn from them."""
	_doctype(doctype)
	return _common.meta_view(doctype)


# --- writes -----------------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def save(doctype, doc, name=None):
	"""Save a record, or a single settings record. Never submits (portal_common.save_doc)."""
	_writable(doctype)
	_not_kept(doctype, name)
	target = _common.save_doc(doctype, doc, name)
	return {"name": target.name}


@frappe.whitelist(methods=["POST"])
def preview(doctype, doc):
	"""ERPNext's own defaults for an unsaved form. Writes nothing."""
	_writable(doctype)
	return _common.preview_doc(doctype, doc, _PREVIEW.get(doctype, ("set_missing_values",)))


@frappe.whitelist(methods=["POST"])
def act(doctype, name, action, transition=None, reason=None):
	"""Submit, cancel, amend or delete a record."""
	_writable(doctype)
	_not_kept(doctype, name)
	return _common.act_on(doctype, name, action, transition, reason)


@frappe.whitelist(methods=["POST"])
def attach(doctype, docname):
	"""Frappe's own upload_file, private, onto a settings record; Frappe checks the write right."""
	_writable(doctype)
	return _common.attach_file(doctype, docname)


# --- holidays (phase 2) --------------------------------------------------------------------------

FILL_KINDS = {"weekly_off": "get_weekly_off_dates", "local": "get_local_holidays", "clear": "clear_table"}


@frappe.whitelist(methods=["POST"])
def fill_holidays(doc, kind):
	"""ERPNext's own Holiday List helpers on an unsaved form: add the weekly offs between the dates,
	add the country's public holidays (the `holidays` package), or clear the table. Writes nothing --
	the form shows the rows and the person saves them. Answers the holidays table, sorted, and how
	many rows were added."""
	_writable("Holiday List")
	if kind not in FILL_KINDS:
		frappe.throw(_("Unknown holiday helper {0}.").format(kind))
	values = _common.parse_json(doc, {}) or {}
	for field in _common.PROTECTED:
		values.pop(field, None)
	draft = frappe.get_doc({"doctype": "Holiday List", **values})
	if kind != "clear" and not (draft.from_date and draft.to_date):
		frappe.throw(_("Set the list's From and To dates first."))
	before = len(draft.holidays)
	getattr(draft, FILL_KINDS[kind])()
	draft.sort_holidays()
	return {
		"holidays": [
			{k: (str(v) if k == "holiday_date" and v else v) for k, v in row.as_dict().items() if k in ("holiday_date", "description", "weekly_off", "is_half_day")}
			for row in draft.holidays
		],
		"added": max(len(draft.holidays) - before, 0),
	}


# --- leave (phase 3): bulk allocation, as the Leave Control Panel does it ------------------------

_PANEL_FIELDS = (
	"dates_based_on", "leave_period", "from_date", "to_date", "carry_forward", "allocate_based_on_leave_policy",
	"leave_policy", "leave_type", "no_of_days", "company", "branch", "department", "employment_type", "designation",
	"employee_grade",
)


def _panel(spec):
	"""An in-memory Leave Control Panel from the screen's choices: HRMS's own rules for which employees
	qualify and which fields each way of allocating needs. Never saved."""
	values = _common.parse_json(spec, {}) or {}
	unknown = set(values) - set(_PANEL_FIELDS)
	if unknown:
		frappe.throw(_("Unknown allocation fields: {0}.").format(", ".join(sorted(unknown))))
	if values.get("dates_based_on") not in ("Leave Period", "Joining Date", "Custom Range"):
		frappe.throw(_("Choose what the dates are based on: a leave period, the joining date, or a custom range."))
	panel = frappe.get_doc({"doctype": "Leave Control Panel", **values})
	panel.allocate_based_on_leave_policy = 1 if values.get("allocate_based_on_leave_policy") else 0
	return panel


@frappe.whitelist(methods=["POST"])
def leave_candidates(spec):
	"""The employees the allocation would cover: those matching the filters who have no leave
	allocation in the dates yet (HRMS's own Leave Control Panel.get_employees)."""
	_writable("Leave Allocation")
	panel = _panel(spec)
	rows = panel.get_employees([]) or []
	return [
		{"employee": r.get("employee") or r.get("name"), "employee_name": r.get("employee_name"), "department": r.get("department"),
		 "date_of_joining": str(r.get("date_of_joining")) if r.get("date_of_joining") else None}
		for r in rows
	]


@frappe.whitelist(methods=["POST"])
def allocate_leave(spec, employees):
	"""Allocate to the chosen employees, one savepoint each, exactly as the Leave Control Panel does
	(a Leave Policy Assignment per employee when allocating by policy, else a Leave Allocation of the
	leave type) -- but answering who succeeded and why anyone failed, where the panel only publishes
	a realtime message. Submits what it makes."""
	from frappe.utils import cint, flt

	_writable("Leave Allocation")
	_writable("Leave Policy Assignment")
	panel = _panel(spec)
	employees = _common.parse_json(employees, []) or []
	if not employees:
		frappe.throw(_("Choose at least one employee."))
	if len(employees) > 500:
		frappe.throw(_("At most 500 employees in one allocation."))
	panel.validate_fields(employees)
	from_date, to_date = panel.get_from_to_date()
	by_policy = cint(panel.allocate_based_on_leave_policy)
	done, failed = [], []
	for employee in employees:
		savepoint = "folt_bulk_leave"
		frappe.db.savepoint(savepoint)
		try:
			start = from_date or frappe.db.get_value("Employee", employee, "date_of_joining")
			if by_policy:
				doc = frappe.new_doc("Leave Policy Assignment")
				doc.update({
					"employee": employee,
					"assignment_based_on": None if panel.dates_based_on == "Custom Range" else panel.dates_based_on,
					"leave_policy": panel.leave_policy, "effective_from": start, "effective_to": to_date,
					"leave_period": panel.get("leave_period"), "carry_forward": panel.carry_forward,
				})
			else:
				doc = frappe.new_doc("Leave Allocation")
				doc.update({
					"employee": employee, "leave_type": panel.leave_type, "from_date": start, "to_date": to_date,
					"carry_forward": cint(panel.carry_forward), "new_leaves_allocated": flt(panel.no_of_days),
				})
			doc.insert()
			doc.submit()
			done.append({"employee": employee, "doctype": doc.doctype, "name": doc.name})
		except Exception as e:  # noqa: BLE001 -- one employee's refusal must not stop the rest
			frappe.db.rollback(save_point=savepoint)
			failed.append({"employee": employee, "message": frappe.utils.strip_html(str(e)).strip()[:300] or type(e).__name__})
		frappe.clear_messages()
	return {"done": done, "failed": failed}


# --- pay set-up (phase 4): a salary slip preview ---------------------------------------------------


@frappe.whitelist()
def preview_salary_slip(structure, employee, posting_date=None):
	"""What a structure would pay an employee for the period of `posting_date`: HRMS's own
	make_salary_slip(for_preview=1), which computes a slip from the employee's assignment and
	attendance without saving it. Run inside a savepoint that is always rolled back, so nothing it
	might touch stays. Payroll figures: an editor who also holds a payroll role."""
	from frappe.utils import flt, nowdate
	from hrms.payroll.doctype.salary_structure.salary_structure import make_salary_slip

	from folt_customizations.salary_slip_access import is_payroll_user

	_writable("Salary Structure")
	if not is_payroll_user():
		frappe.throw(_("Salary previews are for payroll roles."), frappe.PermissionError)
	if not frappe.db.exists("Salary Structure", {"name": structure, "docstatus": 1}):
		frappe.throw(_("{0} is not a submitted salary structure.").format(structure))
	savepoint = "folt_slip_preview"
	frappe.db.savepoint(savepoint)
	try:
		slip = make_salary_slip(structure, employee=employee, posting_date=posting_date or nowdate(), for_preview=1)
		lines = lambda rows: [  # noqa: E731
			{"component": r.salary_component, "abbr": r.abbr, "amount": flt(r.amount, 2)}
			for r in rows if not (r.get("statistical_component") or r.get("do_not_include_in_total"))
		]
		return {
			"structure": structure, "employee": slip.employee,
			"employee_name": slip.employee_name or frappe.db.get_value("Employee", slip.employee, "employee_name"),
			"start_date": str(slip.start_date), "end_date": str(slip.end_date), "currency": slip.currency,
			"total_working_days": flt(slip.total_working_days), "payment_days": flt(slip.payment_days),
			"gross_pay": flt(slip.gross_pay, 2), "total_deduction": flt(slip.total_deduction, 2), "net_pay": flt(slip.net_pay, 2),
			"earnings": lines(slip.earnings), "deductions": lines(slip.deductions),
		}
	finally:
		frappe.db.rollback(save_point=savepoint)
		frappe.clear_messages()


@frappe.whitelist()
def link_search(doctype, txt="", filters=None, page_length=20):
	"""Frappe's own link search, for the settings forms' link fields."""
	_reader()
	return _common.link_search(doctype, txt, filters, page_length)
