// Signing an approval, and seeing who signed.
//
// Two things on every FoLT workflow form (the doctypes in `frappe.boot.folt_signing`, from
// approval_signatures.signing_map -- every active workflow but master data):
//
//   1. Taking an approval step asks for a signature first: a pad, prefilled with the signature the
//      user keeps, and a box to keep this one for next time. The signature is parked on the server
//      (approval_signatures.hold_signature) and stamped onto the step by the save the action makes
//      -- the same hand-off folt_workflow.js uses for a rejection reason, for the same reason:
//      `apply_workflow` reloads the document and drops anything else the browser sent.
//
//   2. An "Approvals" panel lists each step with the approver's name, designation, the date and
//      their signature. The same steps are what the prints sign with (print_format_templates).
//
// Which moves ask for a signature is derived on the server: every forward move out of a draft
// state, which is the approval phase of every FoLT chain. Turn-downs ask for a reason instead.

frappe.provide("folt.approvals");

folt.approvals.API = "folt_customizations.approval_signatures";

folt.approvals.is_signing_move = function (frm) {
	const spec = (frappe.boot.folt_signing || {})[frm.doctype];
	if (!spec || !frm.selected_workflow_action) return false;
	const state = frm.doc[spec.state_field];
	return (spec.signs || []).some(
		([from_state, action]) => from_state === state && action === frm.selected_workflow_action
	);
};

folt.approvals.signature_fields = function (saved, intro) {
	return [
		{ fieldtype: "HTML", fieldname: "intro", options: `<p class="text-muted">${intro}</p>` },
		{ fieldtype: "Signature", fieldname: "signature", label: __("Signature"), reqd: 1 },
		{
			fieldtype: "Check",
			fieldname: "remember",
			label: __("Keep this as my signature"),
			default: saved ? 0 : 1,
			description: __("Prefilled next time, and used when you approve from a screen with no signing pad."),
		},
	];
};

folt.approvals.ask_for_signature = function (frm) {
	const action = frm.selected_workflow_action;

	return new Promise((resolve) => {
		// handle_workflow_action froze the page; a frozen page cannot be signed on.
		frappe.dom.unfreeze();

		frappe
			.xcall(`${folt.approvals.API}.my_signature`)
			.catch(() => ({}))
			.then(({ signature: saved } = {}) => {
				const intro = __("{0} {1} as {2}. Your name, designation and this signature go on the document and its print.", [
					__(action),
					frappe.utils.escape_html(frm.doc.name),
					frappe.utils.escape_html(frappe.session.user_fullname || frappe.session.user),
				]);
				const dialog = new frappe.ui.Dialog({
					title: __("Sign: {0}", [__(action)]),
					fields: folt.approvals.signature_fields(saved, intro),
					primary_action_label: __("Sign and {0}", [__(action)]),
					primary_action: ({ signature, remember }) => {
						if (!signature) return;
						dialog.hide();
						frappe.dom.freeze();
						frappe
							.xcall(`${folt.approvals.API}.hold_signature`, {
								doctype: frm.doctype,
								name: frm.doc.name,
								signature,
								remember: remember ? 1 : 0,
							})
							.then(resolve)
							.catch(() => frappe.dom.unfreeze());
					},
				});
				// Dismissing abandons the action: the promise is never resolved, which is how
				// frappe's own before_workflow_action handlers cancel one.
				dialog.show();
				// After show(): the pad sizes its canvas from the rendered dialog.
				if (saved) dialog.set_value("signature", saved);
			});
	});
};

folt.approvals.edit_my_signature = function () {
	frappe
		.xcall(`${folt.approvals.API}.my_signature`)
		.then(({ signature: saved }) => {
			const dialog = new frappe.ui.Dialog({
				title: __("My approval signature"),
				fields: [
					{
						fieldtype: "HTML",
						fieldname: "intro",
						options: `<p class="text-muted">${__("Signed onto the documents you approve. You can still change it each time you sign.")}</p>`,
					},
					{ fieldtype: "Signature", fieldname: "signature", label: __("Signature") },
				],
				primary_action_label: __("Save"),
				primary_action: ({ signature }) => {
					frappe
						.xcall(`${folt.approvals.API}.save_my_signature`, { signature: signature || "" })
						.then(() => {
							dialog.hide();
							frappe.show_alert({ message: __("Signature saved"), indicator: "green" });
						});
				},
			});
			dialog.show();
			if (saved) dialog.set_value("signature", saved);
		});
};

folt.approvals.render_step = function (s) {
	const esc = frappe.utils.escape_html;
	const who = esc(s.approver_name || s.approver || "");
	const capacity = esc(s.designation || (s.role ? __(s.role) : ""));
	const when = frappe.datetime.str_to_user(s.signed_on);
	const sig = s.signature
		? `<img class="folt-approval-sig" src="${esc(s.signature)}" alt="${__("Signature of {0}", [who])}">`
		: s.turn_down
		? ""
		: `<span class="folt-approval-nosig">${__("Not signed")}</span>`;
	const reason = s.reason ? `<div class="folt-approval-reason">${esc(s.reason)}</div>` : "";
	const classes = ["folt-approval"];
	if (s.superseded) classes.push("is-superseded");
	if (s.turn_down) classes.push("is-turn-down");
	const note = s.turn_down ? "" : s.superseded ? `<span class="folt-approval-note">${__("Superseded")}</span>` : "";

	return `<li class="${classes.join(" ")}">
		<div class="folt-approval-step">
			<div class="folt-approval-action">${esc(__(s.action))} ${note}</div>
			<div class="folt-approval-who">${who}${capacity ? ` &middot; ${capacity}` : ""}</div>
			<div class="folt-approval-when">${esc(when)} &rarr; ${esc(__(s.to_state))}</div>
			${reason}
		</div>
		<div class="folt-approval-sigbox">${sig}</div>
	</li>`;
};

folt.approvals.render = function (frm) {
	if (!frm.dashboard || !frm.page || frm.is_new()) return;
	const name = frm.doc.name;

	frappe
		.xcall(`${folt.approvals.API}.get_approvals`, { doctype: frm.doctype, name })
		.catch(() => null)
		.then((data) => {
			// The form may have moved on to another document while this was in flight.
			if (!data || frm.doc.name !== name) return;
			const steps = data.steps || [];
			const list = steps.length
				? `<ol class="folt-approvals-list">${steps.map(folt.approvals.render_step).join("")}</ol>`
				: `<p class="text-muted">${__("Nobody has signed this yet. Each approval step adds the approver's name and signature here and on the print.")}</p>`;
			const body = `<div class="folt-approvals" data-folt-approvals="${frappe.utils.escape_html(name)}">
				${list}
				<button class="btn btn-xs btn-default folt-approvals-mine">${__("My signature")}</button>
			</div>`;

			// Same reasons as folt_guide.js: reset() drops `.custom` sections, and two in-flight
			// renders (save -> reload) must not leave two panels.
			const existing = frm.dashboard.parent.find("[data-folt-approvals]");
			if (existing.length) {
				existing.replaceWith(body);
			} else {
				frm.dashboard.add_section(body, __("Approvals"));
			}
			frm.dashboard.parent
				.find(".folt-approvals-mine")
				.off("click")
				.on("click", () => folt.approvals.edit_my_signature());
			frm.dashboard.show();
		});
};

frappe.after_ajax(() => {
	Object.keys(frappe.boot.folt_signing || {}).forEach((doctype) => {
		frappe.ui.form.on(doctype, {
			refresh: (frm) => folt.approvals.render(frm),
			before_workflow_action: (frm) =>
				folt.approvals.is_signing_move(frm) ? folt.approvals.ask_for_signature(frm) : undefined,
		});
	});
});
