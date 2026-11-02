// Purchase Requisition Register (FoLT procurement, ADR-008): filters for the Desk and for the app (scripts/extract-report-filters.cjs).
frappe.query_reports["Purchase Requisition Register"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company", reqd: 1, default: frappe.defaults.get_user_default("Company") },
		{ fieldname: "from_date", label: __("From"), fieldtype: "Date", reqd: 1, default: frappe.datetime.add_months(frappe.datetime.get_today(), -12) },
		{ fieldname: "to_date", label: __("To"), fieldtype: "Date", reqd: 1, default: frappe.datetime.get_today() },
		{ fieldname: "department", label: __("Department"), fieldtype: "Link", options: "Department" },
		{ fieldname: "workflow_state", label: __("Approval"), fieldtype: "Select", options: "\nDraft\nPending Head of Programs\nPending Head of Finance\nApproved\nRejected" },
	],
};
