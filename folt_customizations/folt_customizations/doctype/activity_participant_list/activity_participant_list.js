// Activity Participant List — the attendance register (W-04A, Module 1 Annex A section 6.4).
// Picking a participant on a row pulls their profile from the master, so a returning
// attendee is never re-keyed by hand.

// The identity fields the master owns. `participant_name` and `mobile_number` are what
// link_or_create_participants matches on, and the rest is what it would otherwise copy
// into a new master record — so the row and the master stay the same person either way.
const PARTICIPANT_PROFILE_FIELDS = [
	"participant_name",
	"mobile_number",
	"id_number",
	"location",
	"gender",
	"is_pwd",
	"photo_consent",
];

frappe.ui.form.on("Activity Participant Entry", {
	participant(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row || !row.participant) return;

		// The server rejects a participant appearing twice on one register (F-04A-E2).
		// Catching it on selection means the clash is resolved while the person picking
		// still knows which row is which, rather than as a throw on save.
		const duplicate = (frm.doc.participants || []).find(
			(other) => other.name !== row.name && other.participant === row.participant
		);

		if (duplicate) {
			frappe.model.set_value(cdt, cdn, "participant", null);
			frappe.msgprint({
				title: __("Already on this register"),
				message: __("{0} is already on row {1}. A participant may attend a session once.", [
					frappe.bold(duplicate.participant_name || duplicate.participant),
					duplicate.idx,
				]),
				indicator: "orange",
			});
			return;
		}

		frappe.db
			.get_value("FoLT Participant", row.participant, PARTICIPANT_PROFILE_FIELDS)
			.then((r) => {
				const profile = r.message;
				// The row can be gone by the time this lands — the grid is editable throughout.
				if (!profile || !locals[cdt][cdn]) return;

				PARTICIPANT_PROFILE_FIELDS.forEach((fieldname) => {
					frappe.model.set_value(cdt, cdn, fieldname, profile[fieldname]);
				});

				frm.refresh_field("participants");
			});
	},
});
