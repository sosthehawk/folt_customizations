// Supplier Compliance (FoLT procurement, ADR-008): filters for the Desk and for the app (scripts/extract-report-filters.cjs).
frappe.query_reports["Supplier Compliance"] = {
	filters: [
		{ fieldname: "supplier_group", label: __("Supplier group"), fieldtype: "Link", options: "Supplier Group" },
	],
};
