"""Idempotent setup for the participant chain (Module 1 Annex A, section 6.4).

Creates the Workflow State / Workflow Action masters, the two workflows, the Employee
Advance project link that scopes the chain, and a starter rate schedule.

Run with:
    bench --site <site> execute folt_customizations.participants_setup.install
"""

import frappe

WORKFLOW_STATES = [
	("Pending Verification", "Warning"),
	("Verified", "Success"),
	("Pending Finance Officer Review", "Warning"),
	("Pending Executive Director Approval", "Warning"),
	("Approved", "Success"),
	("Paid", "Success"),
	("Partly Paid", "Warning"),
	("Disputed", "Danger"),
]

WORKFLOW_ACTIONS = [
	"Submit for Verification",
	"Verify",
	"Submit for Review",
	"Review & Forward",
	"Approve",
	"Return for Correction",
	"Mark Paid",
	"Mark Partly Paid",
	"Raise Dispute",
	"Resolve Dispute",
]

EMPLOYEE_ADVANCE_FIELDS = [
	{
		"fieldname": "folt_project",
		"label": "Activity (Project)",
		"fieldtype": "Link",
		"options": "Project",
		"insert_after": "folt_donor_code",
		"description": (
			"The activity this float funds. Inherited by the attendance register and the "
			"participant reimbursement list, which cannot reach outside it."
		),
	},
	{
		"fieldname": "folt_activity_requisition",
		"label": "Activity Requisition",
		"fieldtype": "Link",
		"options": "Activity Requisition",
		"insert_after": "folt_project",
	},
]


def install():
	create_workflow_states()
	create_workflow_actions()
	create_employee_advance_fields()
	create_participant_list_workflow()
	create_reimbursement_list_workflow()
	create_locations()
	create_default_rate_schedule()
	frappe.db.commit()
	print("participant chain: setup complete")


def create_workflow_states():
	for state, style in WORKFLOW_STATES:
		if frappe.db.exists("Workflow State", state):
			continue
		frappe.get_doc({"doctype": "Workflow State", "workflow_state_name": state, "style": style}).insert(
			ignore_permissions=True
		)
		print(f"  workflow state: {state}")


def create_workflow_actions():
	for action in WORKFLOW_ACTIONS:
		if frappe.db.exists("Workflow Action Master", action):
			continue
		frappe.get_doc(
			{"doctype": "Workflow Action Master", "workflow_action_name": action}
		).insert(ignore_permissions=True)
		print(f"  workflow action: {action}")


def create_employee_advance_fields():
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	create_custom_fields({"Employee Advance": EMPLOYEE_ADVANCE_FIELDS}, ignore_validate=True)

	# Fixtures are filtered on module, so the fields must carry ours to be exported.
	for field in EMPLOYEE_ADVANCE_FIELDS:
		name = f"Employee Advance-{field['fieldname']}"
		if frappe.db.exists("Custom Field", name):
			frappe.db.set_value("Custom Field", name, "module", "Folt Customizations")

	print("  employee advance: folt_project, folt_activity_requisition")


def create_participant_list_workflow():
	"""W-04A: Draft > Pending Verification > Verified, with a route back for correction."""
	upsert_workflow(
		{
			"name": "Activity Participant List Verification",
			"document_type": "Activity Participant List",
			"workflow_state_field": "workflow_state",
			"is_active": 1,
			"send_email_alert": 0,
			"states": [
				{"state": "Draft", "doc_status": "0", "allow_edit": "Employee"},
				{"state": "Pending Verification", "doc_status": "0", "allow_edit": "Head of Programs"},
				{"state": "Verified", "doc_status": "1", "allow_edit": "Head of Programs"},
			],
			"transitions": [
				{
					"state": "Draft",
					"action": "Submit for Verification",
					"next_state": "Pending Verification",
					"allowed": "Employee",
				},
				{
					"state": "Pending Verification",
					"action": "Verify",
					"next_state": "Verified",
					"allowed": "Head of Programs",
				},
				{
					"state": "Pending Verification",
					"action": "Return for Correction",
					"next_state": "Draft",
					"allowed": "Head of Programs",
				},
			],
		}
	)


def create_reimbursement_list_workflow():
	"""W-04B: Draft > Finance review > ED approval > Paid / Partly Paid, with Disputed.

	The payout is money leaving the organisation, so it carries the same two signatures the
	paper reimbursement list does — checked by Finance, approved by the Executive Director —
	rather than the single verification this chain started with. The attendance register it
	derives from keeps its one programme-side verification: that document evidences who turned
	up, not what was paid.
	"""
	upsert_workflow(
		{
			"name": "Participant Reimbursement List Verification",
			"document_type": "Participant Reimbursement List",
			"workflow_state_field": "workflow_state",
			"is_active": 1,
			"send_email_alert": 0,
			"states": [
				{"state": "Draft", "doc_status": "0", "allow_edit": "Employee"},
				{"state": "Pending Finance Officer Review", "doc_status": "0", "allow_edit": "Finance Officer"},
				{"state": "Pending Executive Director Approval", "doc_status": "0", "allow_edit": "Executive Director"},
				{"state": "Approved", "doc_status": "1", "allow_edit": "Finance Officer"},
				{"state": "Partly Paid", "doc_status": "1", "allow_edit": "Finance Assistant"},
				{"state": "Paid", "doc_status": "1", "allow_edit": "Finance Officer"},
				{"state": "Disputed", "doc_status": "1", "allow_edit": "Finance Officer"},
			],
			"transitions": [
				{
					"state": "Draft",
					"action": "Submit for Review",
					"next_state": "Pending Finance Officer Review",
					"allowed": "Employee",
				},
				{
					"state": "Pending Finance Officer Review",
					"action": "Review & Forward",
					"next_state": "Pending Executive Director Approval",
					"allowed": "Finance Officer",
					"allow_self_approval": 0,
				},
				{
					"state": "Pending Finance Officer Review",
					"action": "Return for Correction",
					"next_state": "Draft",
					"allowed": "Finance Officer",
				},
				{
					"state": "Pending Executive Director Approval",
					"action": "Approve",
					"next_state": "Approved",
					"allowed": "Executive Director",
					"allow_self_approval": 0,
				},
				{
					"state": "Pending Executive Director Approval",
					"action": "Return for Correction",
					"next_state": "Draft",
					"allowed": "Executive Director",
				},
				{
					"state": "Approved",
					"action": "Mark Partly Paid",
					"next_state": "Partly Paid",
					"allowed": "Finance Assistant",
				},
				{
					"state": "Approved",
					"action": "Mark Paid",
					"next_state": "Paid",
					"allowed": "Finance Assistant",
				},
				{
					"state": "Partly Paid",
					"action": "Mark Paid",
					"next_state": "Paid",
					"allowed": "Finance Assistant",
				},
				{
					"state": "Paid",
					"action": "Raise Dispute",
					"next_state": "Disputed",
					"allowed": "Finance Officer",
				},
				{
					"state": "Disputed",
					"action": "Resolve Dispute",
					"next_state": "Paid",
					"allowed": "Finance Officer",
				},
			],
		}
	)


def upsert_workflow(spec):
	name = spec["name"]

	if frappe.db.exists("Workflow", name):
		doc = frappe.get_doc("Workflow", name)
		doc.set("states", [])
		doc.set("transitions", [])
	else:
		doc = frappe.new_doc("Workflow")
		doc.workflow_name = name

	doc.document_type = spec["document_type"]
	doc.workflow_state_field = spec["workflow_state_field"]
	doc.is_active = spec["is_active"]
	doc.send_email_alert = spec["send_email_alert"]

	for state in spec["states"]:
		doc.append("states", state)
	for transition in spec["transitions"]:
		doc.append("transitions", transition)

	doc.save(ignore_permissions=True)
	print(f"  workflow: {name}")


# Basin locations and the transport rate bands used on FoLT's activity floats. The bands
# are those on the float request budget of the worked case; they are provisional pending
# open item 8 in the annex (Programs to confirm the authoritative schedule).
LOCATION_RATES = {
	"Kanamkemer": (3000, "Turkana Central"),
	"Township": (3000, "Turkana Central"),
	"Lodwar": (3000, "Turkana Central"),
	"Turkana Central": (3000, "Turkana Central"),
	"Nakalale": (4000, "Turkana Central"),
	"Lokichar": (4000, "Turkana South"),
	"Kakuma": (4000, "Turkana West"),
	"Aroo": (4000, "Turkana West"),
	"Lochor Aikeny": (4000, "Turkana Central"),
	"Lokore": (4000, "Turkana North"),
	"Kalokol": (4000, "Turkana Central"),
	"Turkana South": (4000, "Turkana South"),
	"Loima": (4500, "Loima"),
	"Kaeris": (4500, "Turkana North"),
	"Kerio": (4500, "Turkana Central"),
	"Eliye": (4500, "Turkana Central"),
	"Turkana West": (5000, "Turkana West"),
	"Turkana East": (5000, "Turkana East"),
	"Kibish": (5000, "Turkana North"),
	"Lokitaung": (5000, "Turkana North"),
	"Lokori": (5000, "Turkana East"),
	"Katilu": (5000, "Turkana South"),
	"Kaitese": (4000, "Turkana East"),
	"Turkwel": (4000, "Loima"),
	"Kataboi": (5000, "Turkana Central"),
	"Kangatotha": (5000, "Turkana Central"),
	"Turkana North": (5500, "Turkana North"),
	"Lokwii": (6000, "Turkana East"),
	"Katilia": (6500, "Turkana East"),
	"Napeitom": (8000, "Turkana East"),
	"Oropoi": (8000, "Turkana West"),
	"Urum": (8000, "Turkana West"),
	"Lorengkipi": (8000, "Loima"),
	"Lokiriama": (8000, "Loima"),
	"Kapelibok": (8000, "Turkana North"),
}


def create_locations():
	"""Seed the Location dimension values. The dimension exists but had no values."""
	created = 0
	for location, (_rate, region) in LOCATION_RATES.items():
		if frappe.db.exists("FoLT Location", location):
			continue
		frappe.get_doc(
			{
				"doctype": "FoLT Location",
				"location_name": location,
				"parent_region": region,
				"is_active": 1,
			}
		).insert(ignore_permissions=True)
		created += 1

	print(f"  locations: {created} created, {len(LOCATION_RATES)} total")


def create_default_rate_schedule():
	"""The organisation-wide schedule that rate validation tests each payment against.

	Rates are the bands from the worked case float budget and are provisional pending
	open item 8 in the annex; edit them in the desk rather than here once Programs
	confirms the authoritative schedule.
	"""
	name = "FoLT Participant Transport Rates (provisional)"
	if frappe.db.exists("Participant Rate Schedule", name):
		print(f"  rate schedule: {name} (exists)")
		return

	doc = frappe.get_doc(
		{
			"doctype": "Participant Rate Schedule",
			"schedule_name": name,
			"valid_from": "2026-01-01",
			"is_active": 1,
			"rates": [
				{"location": location, "transport_rate": rate}
				for location, (rate, _region) in LOCATION_RATES.items()
			],
		}
	)
	doc.insert(ignore_permissions=True)
	print(f"  rate schedule: {name} ({len(LOCATION_RATES)} locations)")
