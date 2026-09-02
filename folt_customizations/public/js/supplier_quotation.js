// What happens to a bid once it has been submitted.
//
// A submitted Supplier Quotation looked finished and was, in FoLT's process, the middle of
// something: the buyer's next move is either the Procurement Committee Evaluation that scores
// the bids received against the RFQ, or -- where the purchase is not being competed at all --
// the Derogation / Waiver Request that has to be authorised before anybody orders on it. Neither
// of those was reachable from this form, and nothing on it said which of the two applied.
//
// WHY THIS IS A FORM SCRIPT AND NOT folt_guide.js. The guide renders these buttons for every
// other document on both of FoLT's chains, off `document_guide.get_guide` -- but it renders them
// as part of a workflow step tracker, and a Supplier Quotation has no workflow: erpnext's own
// submit is what advances it, from this form or from the supplier portal. So the one document on
// the procurement chain with no guide gets the same two buttons from its own script, off
// `procurement_chain.get_route`, and runs them through the same `folt.chain.run`.
//
// The server-side half is procurement_chain.py. Everything about which route is offered, and
// whether the reader may take it, is decided there -- this file only draws what it is told.
//
// WHY ERPNEXT'S OWN "Create > Purchase Order" IS LEFT ALONE. It goes straight from a bid to an
// order, which is the move FoLT's procurement policy does not allow -- but it is no longer a way
// round anything: purchase_order.require_award_authority lets that order be prepared and refuses
// to let it leave Draft without an approved award or waiver, naming both. And the path has a
// real use, the one the Implementation Guide describes for single sourcing: a waiver raised on
// its own, with no quotation behind it, authorises an order whose lines have to be keyed by
// hand. Taking the button away would take that with it.
// (It also cannot be taken away reliably from here. ScriptManager.trigger runs every
// `frappe.ui.form.on` handler -- `new_style` -- BEFORE the doctype's own controller, and
// erpnext's SupplierQuotationController.refresh is the controller: a removal in this file races
// the add it is trying to undo, whether it is done synchronously or after the round trip below.)

frappe.ui.form.on("Supplier Quotation", {
	refresh(frm) {
		if (frm.is_new()) return;

		const shown_for = frm.doc.name;

		frappe
			.xcall("folt_customizations.procurement_chain.get_route", {
				doctype: frm.doctype,
				name: frm.doc.name,
			})
			// The route is an explanation, not a control: a form that cannot load it is still a
			// working form, and an error dialog on top of whatever caused it helps nobody. Only
			// the fetch is caught -- see the same reasoning in folt_guide.js.
			.catch(() => null)
			.then((route) => {
				if (!route) return;
				// The form may have moved on while the call was in flight, which would put one
				// bid's route on another.
				if (frm.doc.name !== shown_for) return;

				if (route.note) {
					frm.dashboard.add_comment(route.note, "blue", true);
				}

				(route.handoffs || []).forEach((handoff) => {
					if (!handoff.ready) return;
					frm.add_custom_button(
						__(handoff.label),
						() => folt.chain.run(frm, handoff),
						__("Create")
					);
				});
			});
	},
});
