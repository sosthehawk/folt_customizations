"""Shared participant logic for the activity float chain.

Implements the rules specified in Module 1 Annex A, section 6.4 (W-04A / W-04B):
the attendance register is the source record, the reimbursement list is derived
from it, and both are scoped by the Project that represents the activity.
"""

import re

import frappe
from frappe import _
from frappe.utils import getdate

# Attendee categories. Only categories in ELIGIBLE_CATEGORIES are pulled into a
# reimbursement list by default (F-04-D2). The per-row `eligible_for_reimbursement`
# check remains editable so an exclusion is a visible decision, not an omission.
PARTICIPANT_CATEGORIES = [
	"Community Participant",
	"Elected Representative",
	"County Official",
	"Partner Organisation",
	"FoLT Staff",
	"Other",
]
ELIGIBLE_CATEGORIES = {"Community Participant", "Elected Representative"}

# Acknowledgement of receipt. A signature is not the only acceptable form — a
# thumbprint is equally valid evidence — but "None" blocks retirement of that line
# (F-04-E4).
ACKNOWLEDGEMENT_OPTIONS = ["", "Signature", "Thumbprint", "None"]

RATE_COMPONENTS = ("transport", "sustenance", "accommodation")


def normalise_mobile(number, label=None):
	"""Normalise and validate a Kenyan mobile number (F-04-E3 / F-04A-E2).

	Accepts 07XXXXXXXX, 01XXXXXXXX, 2547XXXXXXXX, +2547XXXXXXXX and returns the
	local 10-digit form, which is what the payout channel and the statement export
	both use. Raises on anything that is not a valid Kenyan mobile number, because
	an invalid destination must be caught at entry rather than at payment.
	"""
	if not number:
		return number

	digits = re.sub(r"[^0-9]", "", str(number))

	if digits.startswith("254"):
		digits = "0" + digits[3:]
	elif len(digits) == 9 and digits[0] in ("7", "1"):
		digits = "0" + digits

	if not re.fullmatch(r"0(7|1)[0-9]{8}", digits):
		frappe.throw(
			_("{0} is not a valid Kenyan mobile number{1}. Expected 07XXXXXXXX or 01XXXXXXXX.").format(
				frappe.bold(number), _(" for {0}").format(label) if label else ""
			),
			title=_("Invalid payout destination"),
		)

	return digits


def check_duplicates(rows, doctype_label):
	"""Reject duplicate participants and duplicate destinations within one document.

	Applies to both the attendance register and the reimbursement list: a shared
	number is resolved at the register (F-04A-E2) so that it can never reach a
	payment instruction (F-04-E1 / F-04-E2).
	"""
	seen_participant = {}
	seen_mobile = {}

	for row in rows:
		participant = getattr(row, "participant", None)
		if participant:
			if participant in seen_participant:
				frappe.throw(
					_("{0} appears twice on this {1} — rows {2} and {3}.").format(
						frappe.bold(row.participant_name or participant),
						doctype_label,
						seen_participant[participant],
						row.idx,
					),
					title=_("Duplicate participant"),
				)
			seen_participant[participant] = row.idx

		mobile = getattr(row, "mobile_number", None)
		if mobile:
			if mobile in seen_mobile:
				frappe.throw(
					_(
						"Mobile number {0} is used by more than one person on this {1} — rows {2} and {3}. "
						"Resolve this before the document is submitted; two payees cannot share one destination."
					).format(frappe.bold(mobile), doctype_label, seen_mobile[mobile], row.idx),
					title=_("Duplicate payout destination"),
				)
			seen_mobile[mobile] = row.idx


def get_rate_schedule(project=None, on_date=None):
	"""Return the rate schedule that applies to a project on a date (W-04B derivation).

	A schedule tied to the project wins over an organisation-wide one, so a project
	may set its own rates without disturbing the default.
	"""
	on_date = getdate(on_date or frappe.utils.nowdate())

	filters = [
		["docstatus", "<", 2],
		["is_active", "=", 1],
		["valid_from", "<=", on_date],
	]

	candidates = frappe.get_all(
		"Participant Rate Schedule",
		filters=filters,
		or_filters=[["valid_upto", ">=", on_date], ["valid_upto", "is", "not set"]],
		fields=["name", "project"],
		order_by="valid_from desc",
	)

	if not candidates:
		return None

	for row in candidates:
		if project and row.project == project:
			return frappe.get_doc("Participant Rate Schedule", row.name)

	for row in candidates:
		if not row.project:
			return frappe.get_doc("Participant Rate Schedule", row.name)

	return None


def get_rates_for_location(schedule, location):
	"""Return the component rates for a location from a schedule, or zeros."""
	blank = {component: 0 for component in RATE_COMPONENTS}
	if not schedule or not location:
		return blank

	for row in schedule.rates:
		if row.location == location:
			return {component: row.get(f"{component}_rate") or 0 for component in RATE_COMPONENTS}

	return blank
