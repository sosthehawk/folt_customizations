// Activity Participant List — the attendance register (W-04A, Module 1 Annex A section 6.4).
// Picking a participant on a row pulls their profile from the master, so a returning
// attendee is never re-keyed by hand.
//
// Two actions sit on top of that, and together they are the loop this form exists to serve:
// *Autofill roster* fills the register from an earlier session before the activity, so the
// FoLT Attendance Sheet can be printed with the names already on it; *Reconcile signed sheet*
// turns the paper that comes back into ticks instead of typing. The register is what the
// reimbursement list derives from, so every keystroke saved here is one that cannot go wrong
// downstream.
//
// Why the loop is shaped that way rather than reading the signed sheet: FoLT's participants
// list is a pre-printed blank form filled in BY HAND at the activity, and handwritten Kenyan
// names and 10-digit numbers do not survive OCR — measured at 38% on names and 0% on phone
// numbers against the real accountability pack. Pre-printing the names removes the handwriting
// instead of trying to read it.

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

frappe.ui.form.on("Activity Participant List", {
	refresh(frm) {
		// Both actions edit the child table, which is not allow_on_submit: the attendees on a
		// verified register are the record the reimbursement list is allowed to derive from, so
		// reconciliation belongs in Draft and a later correction is an amendment.
		if (frm.doc.docstatus !== 0) return;

		if ((frm.doc.participants || []).length) {
			frm.add_custom_button(__("Reconcile signed sheet"), () => reconcile_sheet(frm));
		}

		frm.add_custom_button(__("Import from sheet"), () => import_from_sheet(frm));

		if (frm.doc.activity) {
			// The button is only worth offering when there is a session to copy from, and
			// get_roster_sources returning nothing is how the first register of an activity
			// says so. Failures are swallowed: a missing roster button is a smaller problem
			// than a traceback on every refresh of the form.
			frappe
				.xcall(
					"folt_customizations.folt_customizations.doctype.activity_participant_list.activity_participant_list.get_roster_sources",
					{ register: frm.doc.name }
				)
				.catch(() => null)
				.then((sources) => {
					if (!sources || !sources.length) return;
					if (frm.doc.docstatus !== 0) return;
					frm.add_custom_button(__("Autofill roster"), () =>
						autofill_roster(frm, sources)
					).addClass("btn-primary");
				});
		}

		if ((frm.doc.participants || []).length && !frm.doc.total_attendees) {
			frm.dashboard.set_headline(
				__(
					"This is a roster — nobody is ticked present yet. Print the FoLT Attendance Sheet, take it to the activity, then reconcile the signed paper here."
				)
			);
		}
	},
});

function autofill_roster(frm, sources) {
	if (frm.is_dirty()) {
		frappe.throw(__("Save the register before adding a roster to it."));
	}

	const dialog = new frappe.ui.Dialog({
		title: __("Autofill roster from an earlier session"),
		fields: [
			{
				fieldname: "from_register",
				fieldtype: "Select",
				label: __("Copy the attendees of"),
				reqd: 1,
				// Date and headcount in the label because that is what makes one session
				// distinguishable from another — a bare APL-2026-000NN is a guess.
				options: sources.map((s) => ({
					label: `${frappe.datetime.str_to_user(s.session_date)} · ${
						s.total_attendees || 0
					} ${__("attendees")}${s.venue ? ` · ${s.venue}` : ""} · ${s.name}`,
					value: s.name,
				})),
				default: sources[0].name,
			},
			{
				fieldname: "attended_only",
				fieldtype: "Check",
				label: __("Only people who were ticked present at that session"),
				default: 1,
			},
		],
		primary_action_label: __("Add to roster"),
		primary_action(values) {
			dialog.hide();
			frappe.call({
				method: "folt_customizations.folt_customizations.doctype.activity_participant_list.activity_participant_list.autofill_roster",
				args: {
					register: frm.doc.name,
					from_register: values.from_register,
					attended_only: values.attended_only ? 1 : 0,
				},
				freeze: true,
				freeze_message: __("Building the roster..."),
				callback: (r) => {
					if (!r.message) return;
					frm.reload_doc();
					report_roster(r.message);
				},
			});
		},
	});
	dialog.show();
}

function report_roster(summary) {
	const parts = [];

	if (summary.added) {
		parts.push(
			__("{0} rows added — the roster is now {1} names.", [
				summary.added,
				summary.roster_rows,
			])
		);
	} else {
		parts.push(__("Nothing was added."));
	}

	if (summary.skipped_existing) {
		parts.push(__("{0} skipped: already on this register.", [summary.skipped_existing]));
	}
	if (summary.truncated) {
		parts.push(
			__("Stopped at {0} rows. Run it again to bring in the rest.", [summary.limit])
		);
	}
	if (summary.cross_project) {
		parts.push(
			__(
				"That session belongs to another activity. Repeat participation across activities is expected, but check the roster reads right."
			)
		);
	}
	if (summary.added) {
		parts.push(
			__(
				"Nobody is ticked present. Print the FoLT Attendance Sheet, then reconcile it when the signed paper comes back."
			)
		);
	}

	frappe.msgprint({
		title: __("Roster"),
		message: parts.join("<br>"),
		indicator: summary.added ? "green" : "orange",
	});
}

function reconcile_sheet(frm) {
	const rows = frm.doc.participants || [];

	const dialog = new frappe.ui.Dialog({
		title: __("Reconcile the signed sheet"),
		size: "large",
		fields: [
			{
				fieldtype: "HTML",
				options: `<p class="text-muted small">${__(
					"Tick every row the sheet was signed against. The list is in the same order as the printed sheet, so you can work straight down the paper."
				)}</p>`,
			},
			{
				fieldname: "present",
				fieldtype: "MultiCheck",
				label: __("Signed the sheet"),
				columns: 2,
				// A real docfield option (multicheck.js): renders Select All / Deselect All,
				// which is the difference between two clicks and fifty-four on a full sheet.
				select_all: 1,
				get_data: () =>
					rows.map((row) => ({
						// row.name, never idx — idx renumbers when a row is deleted, and the
						// grid stays editable while this dialog is open.
						value: row.name,
						label: `${row.idx}. ${frappe.utils.escape_html(
							row.participant_name || __("(no name)")
						)}${row.mobile_number ? ` &middot; ${row.mobile_number}` : ""}`,
						checked: !!row.attended,
					})),
			},
			{ fieldtype: "Section Break" },
			{
				fieldname: "acknowledgement",
				fieldtype: "Select",
				label: __("How the ticked rows acknowledged"),
				options: ["Signature", "Thumbprint", "None"].join("\n"),
				default: "Signature",
				description: __(
					"A thumbprint is evidence exactly as a signature is. Change individual rows in the grid afterwards where a sheet carries both."
				),
			},
			{
				fieldname: "mark_rest_absent",
				fieldtype: "Check",
				label: __("Mark every row not ticked as absent"),
				default: 1,
				description: __(
					"Untick to work through a long sheet in passes — rows you have not reached are then left alone."
				),
			},
		],
		primary_action_label: __("Apply"),
		primary_action(values) {
			const ticked = new Set(values.present || []);
			let present = 0;
			let absent = 0;

			// One pass, then a single refresh_field. frappe.model.set_value per row per field
			// would re-render the grid over a hundred times on a 54-row sheet.
			rows.forEach((row) => {
				if (ticked.has(row.name)) {
					row.attended = 1;
					row.acknowledgement = values.acknowledgement;
					present += 1;
				} else if (values.mark_rest_absent) {
					row.attended = 0;
					// "None" rather than "": somebody has now read the sheet and recorded that
					// this person left no mark, which is a different fact from "not reconciled".
					row.acknowledgement = "None";
					absent += 1;
				}
			});

			frm.refresh_field("participants");
			frm.dirty();
			dialog.hide();

			frappe.show_alert({
				message: __("{0} present, {1} absent — save to update the headcount.", [
					present,
					absent,
				]),
				indicator: present ? "blue" : "orange",
			});
		},
	});
	dialog.show();
}

// Read a TYPED sheet into the grid. The register's own signed sheet is usually handwritten and
// the server refuses those with an explanation, so the field defaults to the attachment but
// stays changeable: the document worth importing is often the typed reimbursement sheet, or the
// Word/Excel original, rather than the scan of the signed paper.
function import_from_sheet(frm) {
	if (frm.is_dirty()) {
		frappe.throw(__("Save the register before importing into it."));
	}

	const dialog = new frappe.ui.Dialog({
		title: __("Import attendees from a document"),
		fields: [
			{
				fieldtype: "HTML",
				options: `<p class="text-muted small">${__(
					"Reads a <b>typed</b> sheet — a scan of one, a PDF, or the Word/Excel original. Handwritten sheets cannot be read; fill the register with <b>Autofill roster</b> and print the FoLT Attendance Sheet instead."
				)}</p>`,
			},
			{
				fieldname: "file_url",
				fieldtype: "Attach",
				label: __("Document to read"),
				default: frm.doc.attendance_sheet || "",
				reqd: 1,
			},
		],
		primary_action_label: __("Read it"),
		primary_action(values) {
			dialog.hide();
			frappe.call({
				method: "folt_customizations.folt_customizations.doctype.activity_participant_list.activity_participant_list.read_attendance_sheet",
				args: { register: frm.doc.name, file_url: values.file_url },
				freeze: true,
				freeze_message: __("Reading the document — this takes a few seconds a page..."),
				callback: (r) => {
					if (!r.message) return;
					apply_sheet_rows(frm, r.message);
				},
			});
		},
	});
	dialog.show();
}

function apply_sheet_rows(frm, result) {
	const rows = result.rows || [];

	if (!rows.length) {
		frappe.msgprint({
			title: __("Nothing to add"),
			message: __("No new attendees were found in that document."),
			indicator: "orange",
		});
		return;
	}

	// Appended UNSAVED, on purpose. An OCR'd row is a proposal: the preparer reads it against
	// the paper in the grid, which already validates numbers and links, and saves when it is
	// right. Nothing reaches the database until they do.
	rows.forEach((row) => {
		const child = frm.add_child("participants");
		Object.keys(row).forEach((field) => {
			if (field.startsWith("_")) return;
			if (row[field] !== null && row[field] !== "") child[field] = row[field];
		});
	});

	frm.refresh_field("participants");
	frm.dirty();

	const parts = [
		__("{0} attendees read from {1} page(s) and added below — <b>nothing is saved yet</b>.", [
			rows.length,
			result.pages,
		]),
	];

	if (result.source === "native") {
		parts.push(
			__("The document had real text in it, so this was read exactly rather than by OCR.")
		);
	}
	if (result.skipped_existing) {
		parts.push(__("{0} skipped: already on this register.", [result.skipped_existing]));
	}
	if (result.signed_marks) {
		parts.push(__("{0} row(s) carried a signature or thumbprint.", [result.signed_marks]));
	}
	(result.warnings || []).forEach((w) => parts.push(w));

	// The flagged rows are the whole point of reviewing. Listed by row number so the preparer
	// can go straight to them rather than re-reading fifty-four.
	const flagged = rows
		.map((row, i) => ({ idx: frm.doc.participants.length - rows.length + i + 1, row }))
		.filter((entry) => (entry.row._flags || []).length);

	if (flagged.length) {
		parts.push(
			`<br><b>${__("Check these {0} row(s) against the paper:", [flagged.length])}</b>`
		);
		flagged.forEach((entry) => {
			parts.push(
				`${entry.idx}. ${frappe.utils.escape_html(entry.row.participant_name)} — ${entry.row._flags.join("; ")}`
			);
		});
	}

	frappe.msgprint({
		title: __("Read from document"),
		message: parts.join("<br>"),
		indicator: flagged.length ? "orange" : "green",
	});
}
