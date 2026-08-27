// What happens next, on the face of the document.
//
// FoLT's finance chain is six documents long and every one of them used to end in a question the
// form could not answer: whose turn is it, and which document comes next? The state field says
// "Approved" and stops there, so the answer lived in the SOP, in somebody's head, or in a
// WhatsApp message -- and the next document was opened blank and re-keyed from this one.
//
// This is the other half of activity_chain.py. The server says where the document sits in the
// SOP, who it is waiting for and what can be raised from it; this renders that as one line and a
// Create button, and the button hands the next document its contents rather than a blank form.
//
// The list of doctypes comes from the boot payload (activity_chain.add_chain_to_bootinfo), so
// there is no second copy of the chain here to fall out of step with it.

frappe.provide("folt.chain");

folt.chain.STATUS = "folt_customizations.activity_chain.get_chain_status";

folt.chain.render = function (frm) {
	// A document that has never been saved has no state, no links and nothing to hand on. The
	// form's own empty-state guidance belongs to it at that point.
	if (frm.is_new()) return;

	frappe.xcall(folt.chain.STATUS, { doctype: frm.doctype, name: frm.doc.name }).then((status) => {
		if (!status) return;

		folt.chain.set_headline(frm, status);
		folt.chain.add_buttons(frm, status);
	});
};

folt.chain.set_headline = function (frm, status) {
	const parts = [];

	if (status.step) {
		parts.push(
			`<b>${__("Step {0} of {1}", [status.step, status.of])}</b> &middot; ${__(status.step_title)}`
		);
	}

	if (status.unassigned) {
		// A role nobody holds means the document is stuck, and no amount of chasing fixes it.
		parts.push(
			`<span class="text-danger">${__("Waiting for {0} — nobody holds that role", [
				status.waiting_for.map((r) => __(r)).join(", "),
			])}</span>`
		);
	} else if (status.waiting_for.length) {
		parts.push(__("Waiting for {0}", [status.waiting_for.map((r) => __(r)).join(__(" or "))]));
	}

	const pending = (status.handoffs || []).filter((h) => h.ready && !h.existing.length);
	if (pending.length) {
		parts.push(
			__("Next: raise the {0}", [pending.map((h) => __(h.label)).join(__(" and the "))])
		);
	}

	const done = (status.handoffs || []).filter((h) => h.existing.length);
	done.forEach((h) => {
		const links = h.existing
			.map((name) => frappe.utils.get_form_link(h.target, name, true))
			.join(", ");
		parts.push(`${__(h.label)}: ${links}`);
	});

	if (parts.length) frm.dashboard.set_headline(parts.join(" &nbsp;|&nbsp; "), "blue");
};

folt.chain.add_buttons = function (frm, status) {
	(status.handoffs || []).forEach((handoff) => {
		if (!handoff.ready) return;

		frm.add_custom_button(
			__(handoff.label),
			() => folt.chain.run(frm, handoff),
			__("Create")
		);
	});

	// Frappe renders the group as a plain dropdown; the chain's next step is the likeliest thing
	// the person on this form wants, so it is given the primary weight the workflow button has.
	if ((status.handoffs || []).some((h) => h.ready && !h.existing.length)) {
		frm.page.set_inner_btn_group_as_primary(__("Create"));
	}
};

folt.chain.run = function (frm, handoff, extra) {
	frappe
		.xcall(handoff.method, Object.assign({ [handoff.arg]: frm.doc.name }, extra || {}))
		.then((result) => {
			// The one hand-off that cannot be decided from the source document alone: a project
			// with two funded floats does not say which of them pays for a list.
			if (result && result.needs_float) {
				folt.chain.ask_which_float(frm, handoff, result);
				return;
			}

			const name = typeof result === "string" ? result : result && result.name;
			if (!name) return;

			frappe.show_alert({
				message: __("{0} {1} created and filled in", [__(handoff.label), name]),
				indicator: "green",
			});
			folt.chain.report(handoff, result);
			frappe.set_route("Form", handoff.target, name);
		});
};

folt.chain.report = function (handoff, result) {
	if (typeof result !== "object") return;

	const lines = [];
	if (result.added !== undefined) {
		lines.push(__("{0} payees pulled from the register", [result.added]));
		if (result.skipped_ineligible) {
			lines.push(
				__("{0} attendees skipped as not eligible by category", [result.skipped_ineligible])
			);
		}
		if (result.no_rate) {
			lines.push(
				__("No rate schedule applies to this activity — each amount has to be entered and justified.")
			);
		}
	}
	if (result.spent !== undefined) {
		lines.push(
			__("{0} accounted for against a float of {1}. Balance {2}.", [
				format_currency(result.spent),
				format_currency(result.float_paid),
				format_currency(result.balance),
			])
		);
		lines.push(__("Add any other receipts — fuel, transaction charges — as further rows."));
	}

	if (lines.length) {
		frappe.msgprint({
			title: __("{0} created", [__(handoff.label)]),
			message: lines.join("<br>"),
			indicator: "blue",
		});
	}
};

folt.chain.ask_which_float = function (frm, handoff, result) {
	frappe.prompt(
		[
			{
				fieldname: "employee_advance",
				fieldtype: "Select",
				label: __("Which float pays for this list?"),
				reqd: 1,
				options: result.floats.map((f) => ({
					value: f.name,
					label: `${f.name} — ${f.employee_name} — ${format_currency(f.paid_amount)}`,
				})),
			},
		],
		(values) => folt.chain.run(frm, handoff, values),
		__("{0} has more than one funded float", [result.activity]),
		__("Create")
	);
};

// Registered per doctype after boot, for the same reason folt_workflow.js is: a form script can
// only be attached by doctype, and the list of doctypes arrives with the boot payload.
frappe.after_ajax(() => {
	Object.keys(frappe.boot.folt_chain || {}).forEach((doctype) => {
		frappe.ui.form.on(doctype, {
			refresh: (frm) => folt.chain.render(frm),
		});
	});
});
