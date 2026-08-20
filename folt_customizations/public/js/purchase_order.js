// Purchase Order is competed by pre-qualified category at FoLT, so the form leads with
// `folt_supplier_group` and derives `supplier` from it. A Property Setter keeps `supplier`
// hidden until a category (or a Derogation / Waiver Request) is on the document; this script
// makes the dropdown obey the category and keeps the two fields from drifting apart.
//
// The matching server-side check lives in folt_customizations/purchase_order.py -- a link
// query only guards the dropdown, not the API.
//
// It also answers "who is this waiting for?" in the form headline, because the workflow state
// alone says what is pending and not who can clear it -- see show_pending_approvers below.

// The headline gets unreadable once a role has many holders, and the point is to name who to
// chase rather than reproduce the Role's user list. The server sends everyone, so the count
// stays honest; the trimming happens here, where the width constraint actually is.
const MAX_NAMED_APPROVERS = 5;

frappe.ui.form.on("Purchase Order", {
	refresh(frm) {
		show_pending_approvers(frm);
	},

	setup(frm) {
		// A supplier can be pre-qualified for several categories (the extras live in
		// `folt_additional_supplier_groups`), so this cannot be a plain link filter on
		// Supplier.supplier_group -- it has to go through the register query.
		frm.set_query("supplier", () => ({
			query: "folt_customizations.supplier.qualified_supplier_query",
			filters: { supplier_group: frm.doc.folt_supplier_group },
		}));
	},

	async folt_supplier_group(frm) {
		const { folt_supplier_group: group, supplier } = frm.doc;
		if (!group || !supplier) return;

		// Changing the category invalidates a supplier awarded under the old one -- but only
		// if it is genuinely not qualified for the new one, so re-picking a category a
		// multi-category supplier also sits in leaves the award alone. `is_qualified` also
		// covers a lapsed `folt_qualified_until`, hence "currently" in the message below.
		const qualified = await frappe.xcall("folt_customizations.supplier.is_qualified", {
			supplier,
			supplier_group: group,
		});
		if (qualified) return;

		await frm.set_value("supplier", null);
		frappe.show_alert({
			message: __("{0} is not currently pre-qualified for {1} — supplier cleared.", [
				supplier,
				group,
			]),
			indicator: "orange",
		});
	},

	async supplier(frm) {
		// Reached when the supplier arrives from somewhere other than the dropdown: an
		// amendment, or "Get Items From > Supplier Quotation". Record the category it was
		// competed in rather than leaving the mandatory field for the user to guess at.
		if (!frm.doc.supplier || frm.doc.folt_supplier_group) return;

		const { message } = await frappe.db.get_value(
			"Supplier",
			frm.doc.supplier,
			"supplier_group"
		);
		if (message?.supplier_group) {
			frm.set_value("folt_supplier_group", message.supplier_group);
		}
	},
});

// Name the people who can move the order out of the state it is sitting in, in the form
// headline. Driven off the Workflow definition server-side (folt_customizations/workflow.py),
// so it follows a workflow edit instead of restating the approval chain here.
//
// This is deliberately not limited to "Pending Approval". Every non-final state answers the
// same question, and a rule keyed on one state's name would go quietly wrong the day a state
// is renamed -- the server returns nothing for a final state, which is the signal to clear.
async function show_pending_approvers(frm) {
	// A headline is per-form, not per-render: clear first, or a document that stops being
	// pending keeps displaying the answer from the last one that was.
	frm.dashboard.clear_comment();
	if (frm.is_new()) return;

	const pending = await frappe.xcall(
		"folt_customizations.workflow.get_pending_approvers",
		{ doctype: frm.doc.doctype, name: frm.doc.name }
	);
	if (!pending?.roles?.length) return;

	const roles = pending.roles.map((role) => __(role)).join(", ");

	if (pending.unassigned) {
		// Nobody holds the role, so the order cannot move at all. Red, because this is a
		// configuration fault rather than a queue someone is working through.
		frm.dashboard.add_comment(
			__("Waiting on {0}, but nobody holds that role — this order cannot be approved until someone is assigned it.", [
				`<b>${roles}</b>`,
			]),
			"red",
			true
		);
		return;
	}

	const shown = pending.approvers.slice(0, MAX_NAMED_APPROVERS);
	const names = shown.map((a) => frappe.utils.escape_html(a.full_name));
	const hidden = pending.approvers.length - shown.length;
	if (hidden > 0) names.push(__("and {0} more", [hidden]));

	frm.dashboard.add_comment(
		__("Waiting on {0}: {1}", [`<b>${roles}</b>`, names.join(", ")]),
		"orange",
		true
	);
}
