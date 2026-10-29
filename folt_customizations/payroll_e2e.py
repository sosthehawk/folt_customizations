"""Known-answer check of the FoLT Kenya Payroll structure against the statutory rates.

Builds a real salary slip through HRMS for a throwaway employee at several gross pays and compares
every statutory line with the figure worked out by hand from the law in force (2026):

  NSSF                     6% of pensionable pay up to the upper earnings limit, KES 108,000
                           (NSSF Act 2013, year 4 rates from February 2026): at most 6,480.
  SHIF                     2.75% of gross pay, minimum KES 300, no cap (Social Health Insurance
                           Act 2023).
  Affordable Housing Levy  1.5% of gross pay (Affordable Housing Act 2024).
  Taxable pay              gross - NSSF - SHIF - Housing Levy (all three deductible before PAYE
                           since the Tax Laws (Amendment) Act 2024).
  PAYE                     10% to 24,000 / 25% to 32,333 / 30% to 500,000 / 32.5% to 800,000 /
                           35% above, monthly; less personal relief of KES 2,400; never below 0.

The 100,000 case is the worked example published by Kenyan payroll guides (PAYE 19,308.35); the
others pin each edge: the SHIF floor, a pay that ends in no tax at all, the NSSF cap, and the top
two bands. Every component rounds to the whole shilling (round_to_the_nearest_integer, as Kenyan
payslips do), so the slip shows 19,308 where the law's arithmetic gives 19,308.35: the table holds
the exact figure and the slip is compared against it rounded.

Taxable Pay and the employer-side lines (NSSF Employer, Housing Levy Employer) are statistical
components: HRMS computes them for other formulas to use but never puts them on the slip. So they
are checked by what depends on them -- PAYE is computed from Taxable Pay, and net pay must be the
gross less the employee's four deductions only. Also checks that each statutory deduction posts to
its own payable account (hr_accounts).

Everything is created inside the run's transaction and rolled back. Run with:

    bench --site <site> execute folt_customizations.payroll_e2e.run
"""

import frappe
from frappe.utils import flt

from folt_customizations.hr_accounts import PAYABLES, TYPED_DEFAULTS

STRUCTURE = "FoLT Kenya Payroll"
PERIOD = ("2026-10-01", "2026-10-31")

# gross -> (NSSF, SHIF, Housing Levy, Taxable Pay, PAYE), worked by hand from the rates above.
CASES = {
	8000: (480.00, 300.00, 120.00, 7100.00, 0.00),  # SHIF floor; tax below the relief
	20000: (1200.00, 550.00, 300.00, 17950.00, 0.00),  # 10% band, wholly relieved
	100000: (6000.00, 2750.00, 1500.00, 89750.00, 19308.35),  # the published worked example
	150000: (6480.00, 4125.00, 2250.00, 137145.00, 33526.85),  # NSSF at its cap
	1000000: (6480.00, 27500.00, 15000.00, 951020.00, 292740.35),  # 32.5% and 35% bands
}

PASS: list[str] = []
FAIL: list[str] = []


def check(label, condition, detail=""):
	(PASS if condition else FAIL).append(label)
	print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")


def shillings(amount: float) -> float:
	"""Half up to the whole shilling, as round_to_the_nearest_integer does."""
	return float(int(amount + 0.5))


def paye_by_hand(taxable: float) -> float:
	bands = [(24000, 0.10), (32333, 0.25), (500000, 0.30), (800000, 0.325), (float("inf"), 0.35)]
	tax, lower = 0.0, 0.0
	for upper, rate in bands:
		if taxable > lower:
			tax += (min(taxable, upper) - lower) * rate
		lower = upper
	return max(0.0, round(tax - 2400, 2))


def run():
	frappe.set_user("Administrator")
	frappe.flags.mute_emails = True
	try:
		_run()
	finally:
		frappe.db.rollback()

	print(f"\nRESULT: {len(PASS)} passed, {len(FAIL)} failed")
	if FAIL:
		for label in FAIL:
			print(f"  FAILED: {label}")
		raise SystemExit(1)


def _run():
	from hrms.payroll.doctype.salary_structure.salary_structure import make_salary_slip

	company = frappe.db.get_value("Salary Structure", STRUCTURE, "company")
	check(f"{STRUCTURE} exists and is submitted", bool(company) and frappe.db.get_value("Salary Structure", STRUCTURE, "docstatus") == 1)
	if not company:
		return

	print("\n--- the hand calculation agrees with the expected table ---")
	for gross, expected in CASES.items():
		check(f"PAYE by hand on {gross:,} gross matches the table", paye_by_hand(expected[3]) == expected[4], f"{paye_by_hand(expected[3])}")

	print("\n--- each statutory deduction posts to its own payable ---")
	for component, account_name in PAYABLES.items():
		account = frappe.db.get_value("Salary Component Account", {"parent": component, "company": company}, "account")
		check(f"{component} posts to {account_name}", bool(account) and account.startswith(account_name), str(account))

	holidays = frappe.get_doc(
		{"doctype": "Holiday List", "holiday_list_name": "E2E Payroll Holidays", "from_date": "2026-01-01", "to_date": "2026-12-31"}
	).insert(ignore_permissions=True)

	for field, account_type in TYPED_DEFAULTS.items():
		account = frappe.db.get_value("Company", company, field)
		check(f"{field} is typed {account_type} (HRMS refuses it otherwise)", frappe.db.get_value("Account", account, "account_type") == account_type, str(account))

	print("\n--- salary slips, line by line ---")
	for gross, (nssf, shif, ahl, taxable, paye) in CASES.items():
		employee = frappe.get_doc(
			{
				"doctype": "Employee",
				"first_name": f"E2E Payroll {gross}",
				"gender": "Female",
				"date_of_birth": "1990-01-01",
				"date_of_joining": "2026-01-01",
				"company": company,
				"holiday_list": holidays.name,
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Salary Structure Assignment",
				"employee": employee.name,
				"salary_structure": STRUCTURE,
				"company": company,
				"from_date": "2026-07-01",
				"base": gross,
			}
		).submit()
		slip = make_salary_slip(STRUCTURE, employee=employee.name, posting_date=PERIOD[1], for_preview=1)
		lines = {row.salary_component: flt(row.amount, 2) for row in slip.earnings + slip.deductions}
		# A line that comes to nothing (PAYE under the relief) is left off the slip: that is a zero.
		got = tuple(lines.get(c, 0.0) for c in ("NSSF", "SHIF", "Affordable Housing Levy", "PAYE"))
		want = tuple(shillings(x) for x in (nssf, shif, ahl, paye))
		check(
			f"{gross:,} gross: NSSF {want[0]:,.0f}, SHIF {want[1]:,.0f}, Levy {want[2]:,.0f}, PAYE {want[3]:,.0f} (exact {paye:,.2f})",
			got == want,
			f"got {got}",
		)
		check(
			f"{gross:,} gross: net pay is gross less the employee's four deductions only",
			flt(slip.net_pay, 2) == flt(gross - sum(want), 2),
			f"net {slip.net_pay}, expected {gross - sum(want)}",
		)
		check(
			f"{gross:,} gross: no statistical line (taxable pay, employer NSSF or Levy) is deducted",
			not {"Taxable Pay", "NSSF Employer", "Housing Levy Employer"} & {row.salary_component for row in slip.deductions},
			str(sorted(row.salary_component for row in slip.deductions)),
		)
