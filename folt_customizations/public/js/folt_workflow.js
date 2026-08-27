// Turning a FoLT document down asks why, before it happens.
//
// Every FoLT approval chain can send a document back for correction or turn it down outright, and
// the person on the other end of that is being asked to do something about it. The server makes
// the reason compulsory (workflow_access.require_rejection_reason); this is the half that asks for
// it at the moment of the decision, so nobody meets the rule as an error message after the fact.
//
// The list of actions that count is derived from the workflows on the server and arrives in the
// boot payload as `folt_turn_downs` (workflow_access.add_turn_downs_to_bootinfo) -- there is no
// second list of action names here to fall out of step with the chains.

frappe.provide("folt.workflow");

// The reason cannot travel on the document: frappe's workflow button posts the action to
// `apply_workflow`, which reloads the document from the database and discards everything the
// browser sent. So it is handed over separately and picked up server-side by the save that the
// action then makes -- see workflow_access.hold_rejection_reason for why not simply save it first.
folt.workflow.HOLD = "folt_customizations.workflow_access.hold_rejection_reason";

folt.workflow.is_turn_down = function (frm) {
	const spec = (frappe.boot.folt_turn_downs || {})[frm.doctype];
	if (!spec || !frm.selected_workflow_action) return false;

	const state = frm.doc[spec.state_field];
	return (spec.turn_downs || []).some(
		([from_state, action]) => from_state === state && action === frm.selected_workflow_action
	);
};

folt.workflow.ask_for_reason = function (frm) {
	const action = frm.selected_workflow_action;

	return new Promise((resolve) => {
		// handle_workflow_action freezes the page before triggering this, and a frozen page cannot
		// be typed into. Resolving re-freezes it, so the action carries on looking like one action.
		frappe.dom.unfreeze();

		const dialog = new frappe.ui.Dialog({
			title: __("{0}: give a reason", [__(action)]),
			fields: [
				{
					fieldname: "reason",
					fieldtype: "Small Text",
					label: __("Reason"),
					reqd: 1,
					description: __(
						"This goes on the document and into its timeline, and is what the next person sees."
					),
				},
			],
			primary_action_label: __(action),
			primary_action: ({ reason }) => {
				if (!(reason || "").trim()) return;
				dialog.hide();
				frappe.dom.freeze();
				frappe
					.xcall(folt.workflow.HOLD, {
						doctype: frm.doctype,
						name: frm.doc.name,
						reason: reason,
					})
					.then(resolve)
					.catch(() => frappe.dom.unfreeze());
			},
		});

		// Dismissing the dialog abandons the action: the promise is simply never resolved, which is
		// how frappe's own before_workflow_action handlers cancel one. The page is already unfrozen.
		dialog.show();
		dialog.get_field("reason").$input.focus();
	});
};

// Registered per doctype because that is the only way frappe takes a form script, and after the
// boot payload has arrived because that is where the list comes from. Forms are only opened after
// boot, so the handlers are always in place before the first one is rendered.
frappe.after_ajax(() => {
	Object.keys(frappe.boot.folt_turn_downs || {}).forEach((doctype) => {
		frappe.ui.form.on(doctype, {
			before_workflow_action: (frm) =>
				folt.workflow.is_turn_down(frm) ? folt.workflow.ask_for_reason(frm) : undefined,
		});
	});
});
