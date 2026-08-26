import frappe

# The Participant Reimbursement List used to be *verified* by a Finance Officer and that was
# the end of it. FoLT's finance workflow puts the payout in front of the Executive Director as
# well, so the single verification step became a review and an approval, and two states were
# renamed to say which is which. Documents already in the old states have to move with them or
# they sit in a state their own workflow no longer contains — the Actions menu goes empty and
# nothing can be paid.
RENAMED_STATES = {
	"Pending Verification": "Pending Finance Officer Review",
	"Verified": "Approved",
}

DOCTYPE = "Participant Reimbursement List"


def execute():
	if not frappe.db.table_exists(DOCTYPE):
		return

	for old_state, new_state in RENAMED_STATES.items():
		frappe.db.set_value(
			DOCTYPE,
			{"workflow_state": old_state},
			"workflow_state",
			new_state,
			update_modified=False,
		)
