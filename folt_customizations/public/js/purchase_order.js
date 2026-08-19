// Purchase Order is competed by pre-qualified category at FoLT, so the form leads with
// `folt_supplier_group` and derives `supplier` from it. A Property Setter keeps `supplier`
// hidden until a category (or a Derogation / Waiver Request) is on the document; this script
// makes the dropdown obey the category and keeps the two fields from drifting apart.
//
// The matching server-side check lives in folt_customizations/purchase_order.py -- a link
// query only guards the dropdown, not the API.

frappe.ui.form.on("Purchase Order", {
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
