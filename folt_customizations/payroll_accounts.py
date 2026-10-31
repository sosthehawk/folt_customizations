"""Payroll ledger accounts, as a setting: which GL account each salary component posts to, and
which bank or cash account salaries are paid from. The FoLT app's HR -> Payroll accounts page.

Who: HR Manager, Accounts Manager, System Manager (EDIT_ROLES), asked before anything else. HR roles
cannot read the chart of accounts and Accounts Manager cannot write Salary Component, so after
that check the reads use get_all and the writes ignore_permissions -- the role gate is the rule.

What is stored, and why twice. A choice is written to the live mapping (Salary Component Account)
so the next posting uses it, AND kept as a default (`folt_payroll_account:<company>:<component>`,
`folt_payroll_payment_account:<company>`), because the salary-component fixture can rewrite the
accounts table on a migrate. hr_accounts.apply_hr_accounts runs after the fixtures and puts every
stored choice back, ahead of its own suggestions.

The payroll payable account is shown, not edited. Every submitted Salary Structure Assignment
carries the payable it was made with, that field cannot change after submission, and a payroll
run only picks employees whose assignment matches the company's payable -- so changing it here
would quietly leave everybody out of the next run.

Rules for an account: it belongs to the company, is a ledger (not a group) and is enabled; an
earning posts to an Expense account, a deduction to a Liability; salaries are paid from an account
of type Bank or Cash. Statistical and not-in-total components never post and take no account.
"""

import json

import frappe
from frappe import _

EDIT_ROLES = {"HR Manager", "Accounts Manager", "System Manager"}
ROOT_TYPE = {"Earning": "Expense", "Deduction": "Liability"}


def _require():
	if frappe.session.user != "Administrator" and not EDIT_ROLES.intersection(frappe.get_roles()):
		frappe.throw(_("Only HR Managers and accounts managers can manage payroll accounts."), frappe.PermissionError)


def _company(company=None):
	company = company or frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)
	if not company or not frappe.db.exists("Company", company):
		frappe.throw(_("No such company."), frappe.DoesNotExistError)
	return company


def component_key(company, component):
	return f"folt_payroll_account:{company}:{component}"


def payment_key(company):
	return f"folt_payroll_payment_account:{company}"


def stored_account(company, component):
	return frappe.db.get_default(component_key(company, component))


def payment_account(company):
	"""The account a payroll run pays salaries from: the stored choice, else the company's bank,
	else its cash account (start_payroll's order before this setting existed)."""
	values = frappe.db.get_value("Company", company, ["default_bank_account", "default_cash_account"], as_dict=True)
	return (
		frappe.db.get_default(payment_key(company))
		or values.default_bank_account
		or values.default_cash_account
		or frappe.db.get_value("Account", {"company": company, "account_type": "Cash", "is_group": 0})
	)


def _leaf_accounts(company, **filters):
	return frappe.get_all(
		"Account",
		filters={"company": company, "is_group": 0, "disabled": 0, **filters},
		fields=["name", "account_name", "account_number"],
		order_by="lft asc",
	)


def _choices(rows):
	return [{"value": r.name, "label": r.name} for r in rows]


def _in_structures():
	"""Components used by an active, submitted salary structure: the ones a run can post."""
	return set(
		frappe.db.sql_list(
			"""select distinct sd.salary_component from `tabSalary Detail` sd
			join `tabSalary Structure` ss on ss.name = sd.parent
			where sd.parenttype = 'Salary Structure' and ss.docstatus = 1 and ss.is_active = 'Yes'"""
		)
	)


def _view(company):
	used = _in_structures()
	mapped = {
		(r.parent): r.account
		for r in frappe.get_all("Salary Component Account", filters={"company": company}, fields=["parent", "account"])
	}
	components = []
	for c in frappe.get_all(
		"Salary Component",
		filters={"disabled": 0},
		fields=["name", "salary_component_abbr", "type", "statistical_component", "do_not_include_in_total"],
		order_by="type desc, name asc",
	):
		posts = not (c.statistical_component or c.do_not_include_in_total)
		components.append(
			{
				"component": c.name,
				"abbr": c.salary_component_abbr,
				"type": c.type,
				"posts": posts,
				"in_structure": c.name in used,
				"account": mapped.get(c.name) or None,
				"stored": bool(stored_account(company, c.name)),
			}
		)
	payable = frappe.db.get_value("Company", company, "default_payroll_payable_account")
	return {
		"company": company,
		"currency": frappe.db.get_value("Company", company, "default_currency"),
		"payable_account": payable,
		"payable_type": payable and frappe.db.get_value("Account", payable, "account_type"),
		"payment_account": payment_account(company),
		"payment_stored": bool(frappe.db.get_default(payment_key(company))),
		"components": components,
		"choices": {
			"expense": _choices(_leaf_accounts(company, root_type="Expense")),
			"liability": _choices(_leaf_accounts(company, root_type="Liability")),
			"payment": _choices(_leaf_accounts(company, account_type=["in", ["Bank", "Cash"]])),
		},
	}


@frappe.whitelist(methods=["GET"])
def get_settings(company=None):
	_require()
	return _view(_company(company))


def _check(company, account, label, root_type=None, account_types=None):
	a = frappe.db.get_value("Account", account, ["company", "is_group", "disabled", "root_type", "account_type"], as_dict=True)
	if not a or a.company != company or a.is_group or a.disabled:
		frappe.throw(_("{0}: {1} is not an enabled ledger account of {2}.").format(label, account, company))
	if root_type and a.root_type != root_type:
		article = _("an") if root_type[0] in "AEIOU" else _("a")
		frappe.throw(_("{0} must post to {1} {2} account; {3} is {4}.").format(label, article, root_type, account, a.root_type))
	if account_types and a.account_type not in account_types:
		frappe.throw(_("{0} must be a Bank or Cash account; {1} is not.").format(label, account))


@frappe.whitelist(methods=["POST"])
def save_settings(company=None, components=None, payment_account=None):
	"""Save the changed mappings. `components` is [{component, account}] (JSON). Everything is
	checked before anything is written, so a bad row changes nothing."""
	_require()
	company = _company(company)
	if isinstance(components, str):
		components = json.loads(components or "[]")
	components = components or []

	for row in components:
		name, account = row.get("component"), row.get("account")
		c = frappe.db.get_value("Salary Component", name, ["type", "statistical_component", "do_not_include_in_total"], as_dict=True)
		if not c:
			frappe.throw(_("No salary component {0}.").format(name), frappe.DoesNotExistError)
		if c.statistical_component or c.do_not_include_in_total:
			frappe.throw(_("{0} never posts, so it takes no account.").format(name))
		if not account:
			frappe.throw(_("Choose an account for {0}.").format(name))
		_check(company, account, name, root_type=ROOT_TYPE.get(c.type))
	if payment_account:
		_check(company, payment_account, _("The salary payment account"), account_types={"Bank", "Cash"})

	for row in components:
		apply_choice(company, row["component"], row["account"])
		frappe.db.set_default(component_key(company, row["component"]), row["account"])
	if payment_account:
		frappe.db.set_default(payment_key(company), payment_account)
	return _view(company)


def apply_choice(company, component, account):
	"""Write one mapping into the component's accounts table (ignore_permissions: see the module
	docstring). A no-op when it is already so."""
	doc = frappe.get_doc("Salary Component", component)
	for row in doc.accounts:
		if row.company == company:
			if row.account == account:
				return
			row.account = account
			break
	else:
		doc.append("accounts", {"company": company, "account": account})
	doc.flags.ignore_permissions = True
	doc.save()
