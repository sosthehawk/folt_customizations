// Where this document is, who has it, and what it still needs -- on the face of the document.
//
// FoLT's chains are between three and six steps long and the form used to show one word of that:
// the state field. "Pending Executive Director Approval" does not say which step of how many, who
// holds that role, that the Head of Finance signed it on Tuesday and why the first attempt came
// back, or that it is going to be refused at the next step for want of a signed list nobody has
// attached. All of that existed; none of it was on the document.
//
// This is the other half of document_guide.py. The server assembles the answer -- and it is
// assembled from the workflows themselves, so a chain that gains a step gains the right tracker
// with it -- and this renders it. There is deliberately no FoLT doctype name anywhere in this
// file: the list of doctypes and the shape of each chain arrive in the boot payload
// (document_guide.add_guide_to_bootinfo), for the same reason folt_workflow.js takes its list of
// turn-down actions from there.
//
// It also absorbs what folt_next_step.js used to do -- "step 3 of 6", "waiting for", and the
// Create button that hands the next document its contents -- because that was the same question
// answered from a second call, and the answer now arrives with everything else.
//
// NOTHING HERE DECIDES ANYTHING. A greyed step and a "blocking" chip are hints about rules
// enforced on the server (workflow_access.enforce_state_custodian, the transitions' own `allowed`
// roles, the doctype controllers). The Actions dropdown is left exactly as frappe built it, so
// there is always a route to an action that does not depend on this file being right.

frappe.provide("folt.guide");
frappe.provide("folt.chain");

folt.guide.GUIDE = "folt_customizations.document_guide.get_guide";

// The kill switch. Every rule in folt_desk.css is nested under this class, so commenting out
// this one line reverts the whole visual change without touching the stylesheet.
folt.guide.enable = function () {
	document.body.classList.add("folt-guided");
};

folt.guide.render = function (frm) {
	// A document that has never been saved has no state, no history and nothing to hand on. The
	// form's own empty state belongs to it at that point -- the folt_next_step.js precedent.
	if (frm.is_new()) return;
	// Quick Entry and any other in-dialog form has no dashboard to render into.
	if (!frm.dashboard || !frm.page) return;

	frappe
		.xcall(folt.guide.GUIDE, { doctype: frm.doctype, name: frm.doc.name })
		// Only the fetch is caught, and the placement of this line is deliberate. A guide that
		// cannot be loaded is not worth an error dialog on top of whatever caused it -- the form
		// is entirely usable without this panel. But a bug in the rendering below is a bug, and
		// catching that too makes it silent: the panel simply never appears, with a clean
		// console and a working form, which is about the least debuggable failure available.
		.catch(() => null)
		.then((guide) => {
			if (!guide || !guide.steps) return;
			// The form may have moved on while the call was in flight -- another refresh, or the
			// user navigating away. Painting then would put one document's guide on another.
			if (frm.doc.name !== guide.name || frm.doctype !== guide.doctype) return;

			folt.guide.paint(frm, guide);
		});
};

// --- painting -------------------------------------------------------------------------------

folt.guide.paint = function (frm, guide) {
	const html = [
		folt.guide.chain_html(guide),
		folt.guide.steps_html(guide),
		folt.guide.turned_down_html(guide),
		folt.guide.panels_html(guide),
		folt.guide.actions_html(guide),
	]
		.filter(Boolean)
		.join("");

	const body = `<div class="folt-guide" data-folt-guide="${frappe.utils.escape_html(guide.name)}">${html}</div>`;

	// WHY THIS IS NOT SIMPLY add_section. `dashboard.reset()` removes `.custom` sections before
	// every refresh, which would be enough on its own -- except that this render is asynchronous.
	// Two refreshes in quick succession (a save, then the reload it triggers) start two calls,
	// and whichever order they resolve in, both would add a section: the tracker appears twice.
	// So the existing block is found and replaced rather than appended to, and the marker is a
	// data attribute in the DOM rather than a handle on `frm`, because `frm` outlives the DOM
	// the dashboard tears down.
	// `parent`, not `wrapper`: FormDashboard stores its container as `this.parent`
	// (form/dashboard.js constructor) and has no `wrapper` at all.
	const existing = frm.dashboard.parent.find("[data-folt-guide]");
	if (existing.length) {
		existing.replaceWith(body);
	} else {
		frm.dashboard.add_section(body, __("Where this is"));
	}

	// `toggle_visibility(false)` leaves `empty-section` on the dashboard, and form.scss makes
	// that `display: none !important` -- so a form that reached this point with the dashboard
	// hidden would render the guide into a hidden box.
	frm.dashboard.show();

	folt.guide.bind(frm, guide);
};

folt.guide.chain_html = function (guide) {
	const chain =
		guide.chain && guide.chain.step
			? `<span class="folt-chain-step">${__("Step {0} of {1}", [
					guide.chain.step,
					guide.chain.of,
				])}</span>
				<span aria-hidden="true">&middot;</span>
				<span>${__(guide.chain.step_title || "")}</span>`
			: "";

	// guide.can_act already says whether the reader is one of the people this document is
	// waiting for. Until now only folt.chain.add_buttons consulted it, so the single fact a
	// reader most wants on opening a document was never stated on its face -- they had to infer
	// it from whether an action button happened to be there.
	//
	// Deliberately NOT synthesising a second "Step N of M" from guide.lane/guide.of when the SOP
	// chain is absent: guide.chain.step counts steps in FoLT's six-document Finance SOP while
	// guide.lane counts lanes within one workflow. Showing one under the other's label would
	// invent a meaning neither has, and the tracker below already conveys lane position.
	const mine = guide.can_act
		? `<span class="folt-chain-mine">${__("It's with you")}</span>`
		: "";

	if (!chain && !mine) return "";

	return `<div class="folt-chain">${chain}${mine}</div>`;
};

folt.guide.steps_html = function (guide) {
	const steps = guide.steps
		.map((step, index) => {
			const classes = ["folt-step", `is-${step.status}`];
			if (guide.at_optional && step.status === "current") classes.push("is-at-optional");

			// A tick for a finished step, the number for everything else: the number is what
			// makes "step 2 of 5" legible, and a step already done does not need counting.
			// The status word is carried in visually-hidden text because the mark conveys it
			// with colour and a glyph alone -- a screen reader would otherwise hear a bare
			// number, or a tick it cannot interpret.
			const status = folt.guide.STEP_STATUS[step.status]
				? __(folt.guide.STEP_STATUS[step.status])
				: "";
			const mark = `<span class="folt-step-mark">${
				step.status === "done" ? "&check;" : index + 1
			}${status ? `<span class="folt-visually-hidden"> ${status}</span>` : ""}</span>`;

			// The role that moves it on from here. A terminal step has nobody, and saying
			// "nobody" there would read as a problem rather than as the end.
			const role = step.roles.length
				? `<span class="folt-step-role">${step.roles.map((r) => __(r)).join(__(" or "))}</span>`
				: "";

			const optional = step.optional.length
				? `<span class="folt-step-optional">${__("or {0}", [
						step.optional.map((s) => __(s)).join(", "),
					])}</span>`
				: "";

			return `<li class="${classes.join(" ")}">
				${mark}
				<span class="folt-step-label">${__(step.label)}</span>
				${role}
				${optional}
			</li>`;
		})
		.join("");

	// tabindex="0" because the rail is `overflow-x: auto` (folt_desk.css) and a scrollable
	// container that nothing can focus is a WCAG 2.1.1 failure -- a keyboard user could not
	// reach steps 5 and 6 of an Employee Advance at all. role="group" plus a label keep the
	// added tab stop from being an anonymous one.
	return `<ol class="folt-steps" tabindex="0" role="group" aria-label="${__(
		"Approval steps"
	)}">${steps}</ol>`;
};

// Rendered into each step mark for screen readers only. A map rather than inline ternaries so
// that a status added to workflow_shape shows up here as an obviously-missing key.
//
// The source strings are plain, and __() is applied at RENDER time rather than here: this file
// is app_include_js and its module body runs before frappe's translations are loaded, so
// calling __() at parse time would freeze the untranslated English into the map for the life of
// the page. Everything else in this file already calls __() inside a render function.
folt.guide.STEP_STATUS = {
	done: "Done",
	current: "Current step",
	ahead: "Not started",
};

folt.guide.turned_down_html = function (guide) {
	if (!guide.off_path || guide.off_path.kind !== "turned_down") return "";

	const reason = guide.rejection_reason
		? `<span>${frappe.utils.escape_html(guide.rejection_reason)}</span>`
		: `<span>${__("No reason was recorded.")}</span>`;

	// role="status" because this banner appears only after the guide's async fetch resolves, so
	// it is never present at page load and a screen reader would otherwise never mention that
	// the document has been sent back. "status" rather than "alert": it is important, but it is
	// the state of the document rather than an interruption.
	return `<div class="folt-turned-down" role="status">
		<span class="folt-turned-down-head">${__("Sent back: {0}", [__(guide.off_path.state)])}</span>
		${reason}
	</div>`;
};

folt.guide.panels_html = function (guide) {
	const panels = [folt.guide.timeline_html(guide), folt.guide.documents_html(guide)]
		.filter(Boolean)
		.join("");

	return panels ? `<div class="folt-panels">${panels}</div>` : "";
};

folt.guide.timeline_html = function (guide) {
	const events = (guide.timeline || [])
		.map((event) => {
			const kind = `is-${(event.kind || "note").replace(/_/g, "-")}`;
			const head =
				event.kind === "raised"
					? __("Raised")
					: event.state
						? __(event.state)
						: frappe.utils.escape_html(event.content || "");

			const when = event.at ? frappe.datetime.str_to_user(event.at) : "";
			const meta = `${frappe.utils.escape_html(event.by_name || event.by || "")}${when ? " &middot; " + when : ""}`;

			// The reason a document was sent back is the one part of a timeline somebody has to
			// act on, so it is shown in full rather than truncated or hidden behind a hover.
			const reason = event.reason
				? `<div class="folt-event-reason">${frappe.utils.escape_html(event.reason)}</div>`
				: "";

			return `<li class="folt-event ${kind}">
				<div class="folt-event-head">${head}</div>
				<div class="folt-event-meta">${meta}</div>
				${reason}
			</li>`;
		})
		.join("");

	const waiting = folt.guide.waiting_html(guide);
	if (!events && !waiting) return "";

	return `<section class="folt-panel">
		<div class="folt-panel-title">${__("Approval timeline")}</div>
		<ul class="folt-timeline">${events}</ul>
		${waiting}
	</section>`;
};

folt.guide.waiting_html = function (guide) {
	const waiting = guide.waiting_for || {};
	const roles = waiting.roles || [];
	if (!roles.length) return "";

	const named = roles.map((r) => __(r)).join(__(" or "));

	// A role nobody holds means the document is stuck, and no amount of chasing a person fixes
	// it -- the fix is a Role assignment. Worth saying loudly rather than showing an empty list.
	if (waiting.unassigned) {
		return `<div class="folt-pending folt-unassigned">${__(
			"Waiting for {0} — nobody holds that role",
			[named]
		)}</div>`;
	}

	const people = (waiting.approvers || [])
		.map((a) => frappe.utils.escape_html(a.full_name))
		.join(", ");

	return `<div class="folt-pending">${__("Waiting for {0}", [named])}${
		people ? `<div class="folt-event-meta">${people}</div>` : ""
	}</div>`;
};

folt.guide.documents_html = function (guide) {
	const documents = guide.documents || [];
	if (!documents.length) return "";

	const rows = documents
		.map((row) => {
			// Three states, and the middle one matters most: `blocks_next` is the difference
			// between "this is missing" and "this is missing and the next step needs it".
			let pill;
			if (row.attached) {
				pill = `<span class="indicator-pill green no-indicator-dot">${__("Attached")}</span>`;
			} else if (row.blocks_next) {
				pill = `<span class="indicator-pill orange no-indicator-dot">${__("Needed next")}</span>`;
			} else if (row.advisory) {
				pill = `<span class="indicator-pill gray no-indicator-dot">${__("Optional")}</span>`;
			} else {
				pill = `<span class="indicator-pill gray no-indicator-dot">${__("Missing")}</span>`;
			}

			// What it will stop, named. "Needed before Verified" is actionable in a way that
			// "Missing" is not.
			const why = row.blocks_next
				? `<span class="folt-document-why">${__("Needed before {0}", [
						row.blocks.map((s) => __(s)).join(__(" or ")),
					])}</span>`
				: row.description
					? `<span class="folt-document-why">${frappe.utils.escape_html(row.description)}</span>`
					: "";

			// aria-label because five buttons all reading "Attach" are indistinguishable to
			// anyone listing the controls on the page. The escape happens BEFORE __() does its
			// interpolation, which is this file's discipline throughout -- __() does not escape
			// its arguments, so escaping after it would be escaping the wrong string.
			const action = row.attached
				? ""
				: `<button type="button" class="btn btn-xs btn-default folt-document-action"
						data-folt-attach="${frappe.utils.escape_html(row.fieldname)}"
						aria-label="${__("Attach {0}", [frappe.utils.escape_html(row.label)])}">${__(
							"Attach"
						)}</button>`;

			// Three cells: label (with `why` under it), status, action. It was previously
			// `flex; justify-content: space-between` with the pill and the button sharing one
			// span that carried the same class as the button inside it -- so on a wide panel the
			// label and the pill were pushed to opposite edges with a void between them, and the
			// wider the panel the less related the two halves looked.
			return `<li class="folt-document ${row.blocks_next ? "is-blocking" : ""}">
				<span class="folt-document-label">${frappe.utils.escape_html(row.label)}${why}</span>
				<span class="folt-document-status">${pill}</span>
				${action}
			</li>`;
		})
		.join("");

	return `<section class="folt-panel">
		<div class="folt-panel-title">${__("Required documents")}</div>
		<ul class="folt-documents">${rows}</ul>
	</section>`;
};

folt.guide.actions_html = function (guide) {
	// The buttons themselves are added in bind(), because they need the transitions frappe has
	// already fetched and a click handler. This is the row they go in, plus the standing warning.
	const blocked = (guide.blocked_by || []).length
		? `<span class="folt-blocked-note">${__("Still needed: {0}", [
				guide.blocked_by.map((label) => frappe.utils.escape_html(label)).join(", "),
			])}</span>`
		: "";

	return `<div class="folt-actions">${blocked}</div>`;
};

// --- behaviour ------------------------------------------------------------------------------

folt.guide.bind = function (frm, guide) {
	const $guide = frm.dashboard.parent.find("[data-folt-guide]");

	$guide.find("[data-folt-attach]").on("click", function () {
		const fieldname = $(this).attr("data-folt-attach");
		const field = frm.fields_dict[fieldname];
		if (!field) return;

		// Go to the field first: it carries the doctype's own label and description, which is
		// the context that says what to attach, and scroll_to_field highlights it. That used to
		// be the whole handler -- and on a short form, where the field is already on screen, a
		// button reading "Attach" scrolled nowhere, opened nothing and read as broken.
		frm.scroll_to_field(fieldname);

		// `disp_status` is what the control itself computed on its last refresh, so this asks
		// exactly the question the field's own Attach button answers: may this be written here,
		// now, by this reader. It already accounts for the workflow's `allow_edit` (frappe marks
		// the whole form read-only off it), docstatus and permlevel, so there is no second copy
		// of those rules here to fall out of step with the first.
		if (field.disp_status === "Write") {
			// The control's own uploader, not a new one: same accept/private options, and the
			// value lands in the model through the control's own on_upload_complete, so an
			// attach from the checklist is indistinguishable from one made at the field.
			field.on_attach_click();
			return;
		}

		// Read-only is usually not a mistake -- it is the workflow saying the document is on
		// somebody else's step -- but silence at that point looks identical to a broken button.
		const waiting = (guide.waiting_for && guide.waiting_for.roles) || [];
		frappe.msgprint({
			title: __("Not editable here"),
			message: waiting.length
				? __("{0} is with {1} at {2}, so it cannot be attached from here. Ask them to attach it, or have the document returned to you.", [
						frappe.bold(field.df.label),
						frappe.bold(waiting.map((r) => __(r)).join(__(" or "))),
						frappe.bold(__(guide.state || "")),
					])
				: __("{0} cannot be attached while this document is read-only.", [
						frappe.bold(field.df.label),
					]),
			indicator: "orange",
		});
	});

	folt.guide.add_actions(frm, guide, $guide.find(".folt-actions"));
	folt.chain.add_buttons(frm, guide);
};

folt.guide.add_actions = function (frm, guide, $row) {
	if (!frm.states || !$row.length) return;
	// An unsaved document's transitions are not the ones the server would apply -- frappe's own
	// show_actions bails out on `__unsaved` for the same reason.
	if (frm.doc.__unsaved) return;

	frappe.workflow.get_transitions(frm.doc).then((transitions) => {
		const allowed = (transitions || []).filter(
			(t) => frappe.user_roles.includes(t.allowed) && folt.guide.may_self_approve(frm, t)
		);
		if (!allowed.length) return;

		const turn_downs = folt.guide.turn_down_actions(frm, guide);
		const forward = allowed.filter((t) => !turn_downs.includes(t.action));
		const back = allowed.filter((t) => turn_downs.includes(t.action));

		// The forward action gets the weight. A turn-down sits beside it as a quiet link: it is
		// already gated by the reason dialog, and giving it an equal button invites the wrong
		// click on a page whose whole purpose is approving things.
		forward.forEach((transition) => {
			$(`<button class="btn btn-primary btn-sm">${__(transition.action)}</button>`)
				.appendTo($row)
				.on("click", () => folt.guide.act(frm, transition));
		});

		back.forEach((transition) => {
			$(`<button class="btn btn-default btn-sm folt-action-secondary">${__(transition.action)}</button>`)
				.appendTo($row)
				.on("click", () => folt.guide.act(frm, transition));
		});
	});
};

// frappe's own has_approval_access, from form/workflow.js. Four of FoLT's transitions set
// allow_self_approval = 0 (the Employee Advance check and approve, and the Finance Officer
// review on the reimbursement list and the expense claim), and the whole point of filtering here
// is to not offer a button the server is going to refuse.
folt.guide.may_self_approve = function (frm, transition) {
	return (
		frappe.session.user === "Administrator" ||
		transition.allow_self_approval ||
		frappe.session.user !== frm.doc.owner
	);
};

folt.guide.turn_down_actions = function (frm, guide) {
	// The same boot payload folt_workflow.js reads, so the two agree on what a turn-down is by
	// construction: the actions that get the reason dialog are exactly the ones shown as
	// secondary here. No second list.
	const spec = (frappe.boot.folt_turn_downs || {})[frm.doctype];
	if (!spec) return [];

	return (spec.turn_downs || [])
		.filter(([from_state]) => from_state === guide.state)
		.map(([, action]) => action);
};

folt.guide.act = function (frm, transition) {
	// frm.states.handle_workflow_action, not a hand-rolled apply_workflow call. That method
	// triggers `before_workflow_action`, which is what folt_workflow.js hangs the "say why" dialog
	// on -- so a turn-down still asks for its reason and still calls hold_rejection_reason before
	// the action runs. Posting apply_workflow directly from here would silently skip both, and the
	// server would refuse the save with "a reason is required" after the fact.
	frm.states.handle_workflow_action(transition);
};

// --- the next document in the chain ---------------------------------------------------------
//
// Kept from folt_next_step.js, which this file replaces. These three encode real decisions --
// which float pays for a list, and what the fetch actually found -- and the payload they read is
// the same `handoffs` get_chain_status has always returned, now arriving inside the guide.

folt.chain.add_buttons = function (frm, guide) {
	(guide.handoffs || []).forEach((handoff) => {
		if (!handoff.ready) return;

		frm.add_custom_button(__(handoff.label), () => folt.chain.run(frm, handoff), __("Create"));
	});

	// The chain's next step is the likeliest thing the person on this form wants -- but only once
	// there is nothing left to do to this document. While a workflow action is still available it
	// keeps the primary weight, because approving what is in front of you comes before raising
	// what comes after it.
	const pending = (guide.handoffs || []).some((h) => h.ready && !h.existing.length);
	if (pending && !guide.can_act) {
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

// Registered per doctype because that is the only way frappe takes a form script, and after the
// boot payload has arrived because that is where the list of doctypes comes from. Forms are only
// opened after boot, so the handlers are always in place before the first one renders.
frappe.after_ajax(() => {
	folt.guide.enable();

	Object.keys(frappe.boot.folt_guide || {}).forEach((doctype) => {
		frappe.ui.form.on(doctype, {
			refresh: (frm) => folt.guide.render(frm),
		});
	});
});
