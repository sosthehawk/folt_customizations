// The waiver's first field asks for two facts the system already has.
//
// "Organisation & Project Name" is one line off FoLT's paper form, and it is mandatory, so a
// waiver opened from the sidebar started with the preparer typing the name of their own employer.
// The waiver raised from a submitted bid has had it filled in since procurement_chain.py existed
// (make_waiver_request); this is the same heading, from the same function, for the form that is
// filled in from scratch.
//
// The heading is composed on the SERVER -- procurement_chain.get_waiver_heading -- rather than
// joined together here. Two reasons: the em dash and the order of the two halves are then stated
// in exactly one place, so a waiver typed by hand and a waiver derived from a bid cannot end up
// with headings that differ by a character; and the organisation is only knowable there, since
// the project's name and the session's default company are both server-side facts.

frappe.ui.form.on("Derogation Waiver Request", {
	onload(frm) {
		fill_heading(frm);
	},

	project(frm) {
		// Picking the project is the other half arriving. Re-derived rather than appended to, so
		// changing the project corrects the heading instead of leaving the old name in it.
		fill_heading(frm);
	},
});

async function fill_heading(frm) {
	// Only ever a suggestion on a form still being filled in. A saved waiver's heading is part of
	// the record -- it is what the Finance Officer and the Executive Director read and signed --
	// and nothing here has any business rewriting it.
	if (!frm.is_new()) return;

	const current = (frm.doc.organisation_project_name || "").trim();
	// Anything the preparer typed themselves stands. `__folt_heading` is what this function last
	// wrote, so a heading it derived can be replaced by a better one when the project is picked,
	// while a heading somebody edited is left exactly as they left it.
	if (current && current !== frm.__folt_heading) return;

	const heading = await frappe.xcall(
		"folt_customizations.procurement_chain.get_waiver_heading",
		{ project: frm.doc.project || null }
	);
	if (!heading || heading === current) return;

	frm.__folt_heading = heading;
	frm.set_value("organisation_project_name", heading);
}
