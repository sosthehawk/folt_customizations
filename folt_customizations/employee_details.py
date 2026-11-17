"""An employee's whole record for HR onboarding: every tab of ERPNext's Employee form, in its order.

ERPNext's Employee form has eight tabs -- Overview, Address & Contacts, Attendance & Leaves, Salary,
Personal Details, Profile, Joining, Exit -- and the app's employee form covered nineteen of their
fields. This module serves the rest to the onboarding screen and saves it, served through
hr_settings_portal (the HR settings edge) and run as the user.

THE LAYOUT IS ERPNEXT'S, READ FROM THE DOCTYPE'S META, so a field HRMS adds in an upgrade appears
here without a code change, in the place the Desk shows it. What is left out is listed in SKIP: the
naming and tree internals, the computed full name, and the user/login switches (the employee page's
Login card owns those).

WHO SEES WHAT IS FRAPPE'S FIELD-LEVEL PERMISSION, not this module's:
  permlevel 0  everything an HR role reads anyway.
  permlevel 1  salary payment details and CTC -- payroll roles read, HR Manager changes.
  permlevel 2  personal details: addresses, emergency contact, marital status, family, blood group,
               health, health insurance, passport, and FoLT's Kenyan statutory numbers (National ID,
               KRA PIN, NSSF, SHA). HR User, HR Manager and System Manager only (permissions.py).
A field the reader cannot read is not sent at all; one they cannot write is sent read only.

WRITES are HR Manager's and System Manager's (the onboarding editors, ADR-009). Only fields this
reader was shown as editable may be sent; anything else is refused by name, before anything is saved.
A child table sent is the whole table. `modified` travels with the save: a newer save by someone
else is refused rather than overwritten.

`missing` is what FoLT asks to have on file before onboarding counts as complete (ONBOARDING_FIELDS),
of the fields this reader can see -- so each person is told only about what they could fill in.
"""

import re

import frappe
from frappe import _
from frappe.utils import cstr

EMPLOYEE = "Employee"
MAX_CHOICES = 500
MAX_ROWS = 100

LAYOUT_TYPES = {"Section Break", "Column Break", "Tab Break", "HTML", "Button", "Fold", "Heading", "Image"}
# Never on this screen: naming, the tree, the computed name, and the login switches the employee
# page's Login card owns (create_employee_login / link_employee_login).
SKIP = {
	"employee", "naming_series", "employee_name", "image", "lft", "rgt", "old_parent", "employee_number",
	"create_user_permission", "create_user_automatically",
}
# Shown, never edited here: the login is linked on the employee page, the applicant is made by the
# onboarding checklist, and the preferred e-mail is computed from the choice above it.
READ_ONLY = {"user_id", "job_applicant", "prefered_email"}
# Plain value types this screen writes; anything else is shown read only.
EDITABLE_TYPES = {
	"Data", "Small Text", "Text", "Long Text", "Text Editor", "Select", "Link", "Date", "Int", "Float",
	"Currency", "Check", "Phone",
}

# What FoLT wants on file before onboarding is done, beyond Frappe's own mandatory fields.
ONBOARDING_FIELDS = (
	"first_name", "gender", "date_of_birth", "date_of_joining", "company", "department", "designation",
	"employment_type", "cell_number", "person_to_be_contacted", "emergency_phone_number", "relation",
	"folt_national_id", "folt_kra_pin", "folt_nssf_number", "folt_sha_number", "salary_mode",
)

KRA_PIN = re.compile(r"^[A-Z]\d{9}[A-Z]$")


def _portal():
	from folt_customizations import hr_settings_portal

	return hr_settings_portal


# --- the layout ---------------------------------------------------------------------------------


def _choices(df, emp):
	"""The values a Link field may take, as {value, label}: what the Desk's own filter would offer."""
	target = df.options
	company = emp.get("company")
	filters, label = {}, None
	if target == "Department":
		filters = {"is_group": 0, **({"company": company} if company else {})}
		label = "department_name"
	elif target == "Cost Center":
		filters = {"is_group": 0, **({"company": company} if company else {})}
		label = "cost_center_name"
	elif target == "Account":
		# employee.js: an Asset, Receivable ledger of the company.
		filters = {"is_group": 0, "root_type": "Asset", "account_type": "Receivable", **({"company": company} if company else {})}
		label = "account_name"
	elif target == "Employee":
		filters = {"status": "Active", "name": ["!=", emp.get("name") or ""]}
		label = "employee_name"
	elif target == "User":
		filters = {"enabled": 1, "user_type": "System User", "name": ["not in", ["Administrator", "Guest"]]}
		label = "full_name"
	elif target == "Currency":
		filters = {"enabled": 1}
	elif target == "Holiday List":
		label = "holiday_list_name"
	elif target in ("Job Applicant",):
		return []  # read only; the value is shown as it is
	meta = frappe.get_meta(target)
	fields = ["name"] + ([label] if label and meta.has_field(label) else [])
	# get_all, not get_list: HR roles hold no read on Account, User or Cost Center, and these are
	# names to choose from on a screen only HR staff open (the edge asks for an HR role first).
	rows = frappe.get_all(target, filters=filters, fields=fields, order_by="name asc", limit_page_length=MAX_CHOICES)
	return [{"value": r.name, "label": cstr(r.get(label) or r.name) if label else r.name} for r in rows]


def _field(df, value, can_write, emp, choice_cache):
	kind = df.fieldtype
	editable = bool(
		can_write
		and kind in EDITABLE_TYPES
		and not df.read_only
		and df.fieldname not in READ_ONLY
	)
	out = {
		"fieldname": df.fieldname,
		"label": _(df.label) if df.label else df.fieldname,
		"fieldtype": kind,
		"reqd": bool(df.reqd),
		"editable": editable,
		"description": _(df.description) if df.description else None,
		"depends_on": df.depends_on or None,
		"value": value if value not in ("",) else None,
		"options": None,
		"choices": None,
	}
	if kind == "Select":
		out["choices"] = [{"value": o, "label": _(o)} for o in (df.options or "").split("\n") if o]
	elif kind == "Link":
		out["options"] = df.options
		if editable:
			key = (df.parent, df.fieldname)
			if key not in choice_cache:
				choice_cache[key] = _choices(df, emp)
			out["choices"] = choice_cache[key]
	return out


def _columns(child, readable, writable, emp, choice_cache):
	meta = frappe.get_meta(child)
	cols = []
	for df in meta.fields:
		if df.fieldtype in LAYOUT_TYPES or df.hidden or df.fieldtype in ("Table", "Table MultiSelect"):
			continue
		if df.permlevel not in readable:
			continue
		cols.append(_field(df, None, df.permlevel in writable, emp, choice_cache))
	return cols


def details_view(employee):
	"""The record as the reader may see it: tabs -> sections -> fields and tables, with values."""
	frappe.has_permission(EMPLOYEE, "read", employee, throw=True)
	doc = frappe.get_doc(EMPLOYEE, employee)
	meta = frappe.get_meta(EMPLOYEE)
	readable = set(meta.get_permlevel_access("read"))
	editor = bool(_portal()._editor()) and doc.has_permission("write")
	writable = set(meta.get_permlevel_access("write")) if editor else set()
	emp = {"name": doc.name, "company": doc.company}
	choice_cache = {}

	tabs, tab, section = [], None, None

	def new_section(label=None, depends_on=None):
		nonlocal section
		section = {"label": _(label) if label else None, "depends_on": depends_on or None, "fields": [], "tables": []}
		tab["sections"].append(section)

	for df in meta.fields:
		if df.fieldtype == "Tab Break":
			tab = {"key": df.fieldname, "label": _(df.label), "sections": []}
			tabs.append(tab)
			new_section()
			continue
		if tab is None:  # fields before the first tab break: ERPNext's Overview
			tab = {"key": "overview", "label": _("Overview"), "sections": []}
			tabs.append(tab)
			new_section()
		if df.fieldtype == "Section Break":
			new_section(df.label, df.depends_on)
			continue
		if df.fieldtype in LAYOUT_TYPES or df.fieldname in SKIP or df.hidden:
			continue
		if df.permlevel not in readable:
			continue
		can_write = df.permlevel in writable
		if df.fieldtype in ("Table", "Table MultiSelect"):
			cols = _columns(df.options, readable, writable if can_write else set(), emp, choice_cache)
			names = [c["fieldname"] for c in cols]
			rows = []
			for row in doc.get(df.fieldname) or []:
				data = row.as_dict(convert_dates_to_str=True)
				rows.append({"name": row.name, **{n: data.get(n) for n in names}})
			section["tables"].append({
				"fieldname": df.fieldname,
				"label": _(df.label),
				"child": df.options,
				"editable": can_write,
				"columns": cols,
				"rows": rows,
			})
			continue
		value = doc.get(df.fieldname)
		if df.fieldtype in ("Date", "Datetime") and value:
			value = cstr(value)
		elif df.fieldtype in ("Currency", "Float"):
			value = float(value or 0)
		elif df.fieldtype in ("Int", "Check"):
			value = int(value or 0)
		section["fields"].append(_field(df, value, can_write, emp, choice_cache))

	# Drop what the reader has nothing in: an empty section (all its fields at a level they cannot
	# read), then a tab left with no section.
	for t in tabs:
		t["sections"] = [s for s in t["sections"] if s["fields"] or s["tables"]]
	tabs = [t for t in tabs if t["sections"]]
	if tabs and tabs[-1]["key"] in ("connections_tab",):
		tabs.pop()

	visible = {f["fieldname"]: f for t in tabs for s in t["sections"] for f in s["fields"]}
	missing = []
	for fieldname in ONBOARDING_FIELDS:
		f = visible.get(fieldname)
		if f is not None and f["value"] in (None, "", 0) and f["fieldtype"] != "Check":
			missing.append({"fieldname": fieldname, "label": f["label"], "tab": next(t["key"] for t in tabs for s in t["sections"] if f in s["fields"])})

	return {
		"employee": doc.name,
		"employee_name": doc.employee_name,
		"modified": cstr(doc.modified),
		"editable": editor,
		"tabs": tabs,
		"missing": missing,
	}


# --- the save -----------------------------------------------------------------------------------


def _editable(view):
	fields = {f["fieldname"]: f for t in view["tabs"] for s in t["sections"] for f in s["fields"] if f["editable"]}
	tables = {tb["fieldname"]: tb for t in view["tabs"] for s in t["sections"] for tb in s["tables"] if tb["editable"]}
	return fields, tables


def _clean_rows(doc, table, rows):
	if not isinstance(rows, list):
		frappe.throw(_("{0} must be a list of rows.").format(table["label"]))
	if len(rows) > MAX_ROWS:
		frappe.throw(_("{0} can hold at most {1} rows here.").format(table["label"], MAX_ROWS))
	mine = {r.name for r in doc.get(table["fieldname"]) or []}
	columns = {c["fieldname"] for c in table["columns"] if c["editable"]}
	out = []
	for row in rows:
		if not isinstance(row, dict):
			frappe.throw(_("Each row of {0} is an object.").format(table["label"]))
		clean = {k: v for k, v in row.items() if k in columns}
		# A row keeps its name only if it is already this employee's: a name from elsewhere would
		# write over that other record's row.
		if row.get("name") in mine:
			clean["name"] = row["name"]
		if any(v not in (None, "") for k, v in clean.items() if k != "name"):
			out.append(clean)
	return out


def save_details(employee, values, modified):
	"""Save the fields and tables sent, then answer the record again."""
	from folt_customizations import portal_common

	if not _portal()._editor():
		frappe.throw(_("Employee details are changed by HR Managers. You can read them."), frappe.PermissionError)
	values = portal_common.parse_json(values, {}) or {}
	if not isinstance(values, dict) or not values:
		frappe.throw(_("Nothing to save."))
	view = details_view(employee)
	if not view["editable"]:
		frappe.throw(_("You cannot change this employee's record."), frappe.PermissionError)
	doc = frappe.get_doc(EMPLOYEE, employee)
	if cstr(modified) != cstr(doc.modified):
		frappe.throw(
			_("Someone saved this record after you opened it. Reload to see their changes."),
			frappe.TimestampMismatchError,
		)

	fields, tables = _editable(view)
	refused = [k for k in values if k not in fields and k not in tables]
	if refused:
		frappe.throw(_("These cannot be changed here: {0}.").format(", ".join(sorted(refused))))

	changes = {}
	for key, value in values.items():
		if key in tables:
			changes[key] = _clean_rows(doc, tables[key], value)
			continue
		f = fields[key]
		if isinstance(value, str):
			value = value.strip()
		if value in ("", None):
			value = None
		elif f["fieldtype"] in ("Select", "Link") and f["choices"] is not None and value not in {c["value"] for c in f["choices"]}:
			frappe.throw(_("{0} is not one of the choices for {1}.").format(frappe.bold(value), f["label"]))
		changes[key] = value

	doc.update(changes)
	doc.save()
	frappe.clear_messages()
	return details_view(doc.name)


# --- Employee.validate: the statutory numbers ---------------------------------------------------


def validate(doc, method=None):
	"""Employee.validate (doc_events): FoLT's Kenyan statutory numbers in the form the authorities
	issue them. Spaces are dropped and letters upper-cased, so a number typed as printed is kept."""
	for field in ("folt_national_id", "folt_kra_pin", "folt_nssf_number", "folt_sha_number"):
		if doc.get(field):
			doc.set(field, re.sub(r"\s+", "", cstr(doc.get(field))).upper())
	if doc.get("folt_kra_pin") and not KRA_PIN.match(doc.folt_kra_pin):
		frappe.throw(
			_("{0} is not a KRA PIN: it is a letter, nine digits and a letter, e.g. A012345678Z.").format(frappe.bold(doc.folt_kra_pin)),
			title=_("KRA PIN"),
		)
	if doc.get("folt_national_id") and not re.fullmatch(r"\d{6,10}", doc.folt_national_id):
		frappe.throw(_("{0} is not a national ID number: it is 6 to 10 digits.").format(frappe.bold(doc.folt_national_id)), title=_("National ID"))
	for field in ("folt_kra_pin", "folt_national_id"):
		value = doc.get(field)
		if value:
			other = frappe.db.get_value(EMPLOYEE, {field: value, "name": ["!=", doc.name or ""]}, "employee_name")
			if other:
				frappe.throw(
					_("{0} is already on {1}'s record.").format(frappe.bold(value), frappe.bold(other)),
					title=_("Duplicate number"),
				)
