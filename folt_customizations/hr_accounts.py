"""The accounts HR and payroll post to: the statutory payables, and the account types HRMS insists on.

The salary components themselves (Basic Salary, NSSF, SHIF, Affordable Housing Levy, PAYE and the
helpers) ship as fixtures, and their formulas carry the law (see the component descriptions and
payroll_e2e for the published worked examples). What a fixture cannot carry is the ledger each
one posts to: account names are per company ("PAYE Payable - FoLT"), and the fixture's `accounts`
table is empty -- so a fixture sync on every `bench migrate` would wipe any account set by hand,
and the next payroll accrual would post nothing to the right place. This module puts them back
on every install and migrate, after the fixtures have synced.

Each deduction gets its own payable, because each is remitted separately and on its own return:

    PAYE                      -> PAYE Payable                KRA, by the 9th of the next month
    NSSF                      -> NSSF Payable                NSSF, by the 9th
    SHIF                      -> SHIF Payable                SHA, by the 9th
    Affordable Housing Levy   -> Housing Levy Payable        KRA, by the 9th
    Basic Salary              -> the company's Salary expense account

Two accounts HRMS refuses to use until they carry the right type, which the standard chart does
not give them:

    Payroll Payable     net pay owed to staff         "Payable"     (a Payroll Entry will not submit)
    Employee Advances   floats paid out to staff      "Receivable"  (an Employee Advance will not save)

Payroll Payable arrives untyped. Employee Advances arrives typed "Payable" in ERPNext 16's standard
chart -- but a float is money the staff member owes back, an asset, and HRMS insists on
"Receivable". So each is corrected from blank, and from that one known-wrong value; any other type
somebody chose stands.

The helper (Taxable Pay) and the employer-side lines (NSSF Employer, Housing Levy Employer) are
statistical components: they never post, so they get no account.

Idempotent: an account that exists is reused, a mapping that is already right is left alone, and
an account somebody chose by hand for a component is never overwritten.
"""

import frappe

# component -> the liability account it posts to (account_name, before the company suffix)
PAYABLES = {
	"PAYE": "PAYE Payable",
	"NSSF": "NSSF Payable",
	"SHIF": "SHIF Payable",
	"Affordable Housing Levy": "Housing Levy Payable",
}
EARNING_ACCOUNT = "Salary"


# company default field -> the account type HRMS requires on it
TYPED_DEFAULTS = {
	"default_payroll_payable_account": "Payable",
	"default_employee_advance_account": "Receivable",
}
# types the standard chart is known to ship that are wrong for that field
KNOWN_WRONG = {"default_employee_advance_account": {"Payable"}}


def apply_hr_accounts():
	"""Hooked on after_install and after_migrate."""
	if not frappe.db.exists("DocType", "Salary Component"):
		return
	for company in frappe.get_all("Company", pluck="name"):
		for field, account_type in TYPED_DEFAULTS.items():
			account = frappe.db.get_value("Company", company, field)
			current = account and frappe.db.get_value("Account", account, "account_type")
			if account and (not current or current in KNOWN_WRONG.get(field, ())):
				frappe.db.set_value("Account", account, "account_type", account_type)
		parent = _duties_and_taxes(company)
		if not parent:
			continue
		for component, account_name in PAYABLES.items():
			if frappe.db.exists("Salary Component", component):
				_map(component, company, _ensure_account(company, parent, account_name))
		salary = frappe.db.get_value(
			"Account", {"company": company, "account_name": EARNING_ACCOUNT, "is_group": 0}, "name"
		)
		if salary and frappe.db.exists("Salary Component", "Basic Salary"):
			_map("Basic Salary", company, salary)


def _duties_and_taxes(company: str) -> str | None:
	"""The group statutory liabilities live under in the standard chart ("Duties and Taxes")."""
	return frappe.db.get_value(
		"Account", {"company": company, "account_name": "Duties and Taxes", "is_group": 1}, "name"
	)


def _ensure_account(company: str, parent: str, account_name: str) -> str:
	name = frappe.db.get_value("Account", {"company": company, "account_name": account_name}, "name")
	if name:
		return name
	account = frappe.get_doc(
		{
			"doctype": "Account",
			"company": company,
			"account_name": account_name,
			"parent_account": parent,
			# No account_type: "Payable" would make ERPNext demand a party on every posting.
			"root_type": "Liability",
			"is_group": 0,
		}
	)
	account.insert(ignore_permissions=True)
	return account.name


def _map(component: str, company: str, account: str):
	doc = frappe.get_doc("Salary Component", component)
	for row in doc.accounts:
		if row.company == company:
			if not row.account:
				row.account = account
				doc.save(ignore_permissions=True)
			return  # a choice somebody made by hand stands
	doc.append("accounts", {"company": company, "account": account})
	doc.save(ignore_permissions=True)
