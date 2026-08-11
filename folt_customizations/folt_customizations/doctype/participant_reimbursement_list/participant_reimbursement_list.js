// Participant Reimbursement List — derived from the attendance register, scoped by project.
// See Module 1 Annex A, section 6.4 (W-04B).

frappe.ui.form.on("Participant Reimbursement List", {
	setup(frm) {
		// Only verified registers for this project are selectable (F-04-D5).
		frm.set_query("attendance_reference", () => {
			return {
				query: "folt_customizations.folt_customizations.doctype.activity_participant_list.activity_participant_list.get_verified_registers",
				filters: { activity: frm.doc.activity },
			};
		});

		frm.set_query("source_attendance_list", "participants", () => {
			return { filters: { activity: frm.doc.activity, docstatus: 1 } };
		});
	},

	refresh(frm) {
		if (frm.doc.docstatus === 0 && frm.doc.activity) {
			frm.add_custom_button(__("Fetch participants from register"), () =>
				fetch_participants(frm)
			).addClass("btn-primary");
		}

		if (frm.doc.docstatus === 0 && !frm.doc.activity) {
			frm.dashboard.set_headline(
				__("Select the float first — the project, the register and the payees all follow from it.")
			);
		}
	},

	employee_advance(frm) {
		// The project is inherited from the float, never chosen twice (annex 6.4.1).
		if (!frm.doc.employee_advance) return;

		frappe.db.get_value("Employee Advance", frm.doc.employee_advance, "folt_project").then((r) => {
			const project = r.message && r.message.folt_project;
			if (project) {
				frm.set_value("activity", project);
			}
		});
	},

	activity(frm) {
		// A register from the previous project can never remain selected.
		if (frm.doc.attendance_reference) {
			frappe.db
				.get_value("Activity Participant List", frm.doc.attendance_reference, "activity")
				.then((r) => {
					if (r.message && r.message.activity !== frm.doc.activity) {
						frm.set_value("attendance_reference", null);
					}
				});
		}
	},
});

function fetch_participants(frm) {
	const run = (register) => {
		frappe.call({
			method: "folt_customizations.folt_customizations.doctype.participant_reimbursement_list.participant_reimbursement_list.fetch_participants",
			args: { reimbursement_list: frm.doc.name, register: register },
			freeze: true,
			freeze_message: __("Fetching participants from the register..."),
			callback: (r) => {
				if (!r.message) return;
				frm.reload_doc();

				const parts = [__("{0} participants added", [r.message.added])];
				if (r.message.skipped_ineligible) {
					parts.push(
						__("{0} attendees skipped as not eligible by category", [
							r.message.skipped_ineligible,
						])
					);
				}
				if (r.message.no_rate) {
					parts.push(
						__("No rate schedule applies to this project — amounts must be entered and justified.")
					);
				}

				frappe.msgprint({
					title: __("Fetched from register"),
					message: parts.join("<br>"),
					indicator: r.message.added ? "green" : "orange",
				});
			},
		});
	};

	if (frm.is_dirty()) {
		frappe.throw(__("Save the list before fetching participants."));
	}

	if (frm.doc.attendance_reference) {
		run(frm.doc.attendance_reference);
		return;
	}

	frappe.prompt(
		[
			{
				fieldname: "register",
				fieldtype: "Link",
				options: "Activity Participant List",
				label: __("Attendance register"),
				reqd: 1,
				get_query: () => ({ filters: { activity: frm.doc.activity, docstatus: 1 } }),
			},
		],
		(values) => run(values.register),
		__("Fetch participants"),
		__("Fetch")
	);
}
