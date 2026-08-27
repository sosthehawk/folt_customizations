// The committee scores the bids that actually came in, so picking the Request for Quotation
// fills the evaluation grid here and now -- one row per member per quotation -- instead of
// leaving somebody to copy quotation numbers and prices across by hand.
//
// This is the immediate feedback, not the rule: sync_quotation_scores() in
// procurement_committee_evaluation.py rebuilds the same grid on every save, so an evaluation
// created over the API, or one whose bids arrive after the RFQ was picked, ends up identical.
// Keep the two in step.

// The fields on each grid that belong to one named member. Kept in step with SELF_ONLY_FIELDS
// in procurement_committee_evaluation.py, which is the rule; this only stops the form inviting
// an edit the server is going to refuse.
const SELF_ONLY_FIELDS = {
	members: ["reviewed", "score", "comments"],
	quotation_scores: ["score", "comments"],
};

frappe.ui.form.on("Procurement Committee Evaluation", {
	refresh(frm) {
		lock_rows_of_other_members(frm);
	},

	request_for_quotation(frm) {
		// Only the RFQ change reports what it found. Changing the committee is a deliberate
		// edit whose effect is visible in the grid; changing the RFQ is where a buyer needs
		// telling that no bids have been received yet, because an empty grid on its own looks
		// like the form is broken.
		sync_quotation_scores(frm, { announce: true });
	},

	members_remove(frm) {
		sync_quotation_scores(frm);
	},
});

frappe.ui.form.on("Procurement Committee Member", {
	member(frm) {
		sync_quotation_scores(frm);
	},
});

// Grey out the cells that belong to the other members, so the committee sees whose row is whose
// instead of discovering it from a validation error after typing. The rule itself is
// enforce_self_scoring() on the server -- this is only the form being honest about it, which is
// why Administrator is left alone: they are exempt there, and a form that disagreed with the
// save would be worse than one that says nothing.
//
// `set_df_property` with a row name reaches the per-row copy of the docfield that each grid row
// renders from (frappe.meta.get_docfield_copy), so this is per row rather than per column, and
// it covers the field wherever it appears -- the grid cell and the expanded row, including the
// `comments` box that is not a grid column at all.
function lock_rows_of_other_members(frm) {
	if (frappe.session.user === "Administrator") return;

	for (const [table, fields] of Object.entries(SELF_ONLY_FIELDS)) {
		for (const row of frm.doc[table] || []) {
			const read_only = row.member === frappe.session.user ? 0 : 1;
			for (const field of fields) {
				frm.set_df_property(table, "read_only", read_only, frm.doc.name, field, row.name);
			}
		}
	}
}

// Carrying scores across a rebuild means matching rows on (member, quotation) -- the same pair
// the server matches on. JSON, rather than joining on a separator, because both halves are
// names: an email address and a document name can contain whatever separator we picked.
const score_key = (member, quotation) => JSON.stringify([member, quotation]);

async function sync_quotation_scores(frm, { announce = false } = {}) {
	const rfq = frm.doc.request_for_quotation;
	if (!rfq) {
		frm.clear_table("quotation_scores");
		frm.refresh_field("quotation_scores");
		return;
	}

	const quotations = await frappe.xcall(
		"folt_customizations.procurement.get_rfq_quotations",
		{ request_for_quotation: rfq }
	);

	const scored = new Map(
		(frm.doc.quotation_scores || []).map((row) => [
			score_key(row.member, row.supplier_quotation),
			row,
		])
	);
	const members = [
		...new Set((frm.doc.members || []).map((row) => row.member).filter(Boolean)),
	];

	frm.clear_table("quotation_scores");
	for (const member of members) {
		for (const quotation of quotations) {
			const previous = scored.get(score_key(member, quotation.supplier_quotation));
			frm.add_child("quotation_scores", {
				member,
				supplier: quotation.supplier,
				supplier_quotation: quotation.supplier_quotation,
				quotation_amount: quotation.grand_total,
				currency: quotation.currency,
				valid_till: quotation.valid_till,
				score: previous?.score,
				comments: previous?.comments,
			});
		}
	}
	frm.refresh_field("quotation_scores");
	// The rebuild replaced every row, so the locks have to go back on the new ones.
	lock_rows_of_other_members(frm);

	if (!announce) return;

	if (!quotations.length) {
		frappe.show_alert({
			message: __("No supplier quotations have been received against {0} yet.", [rfq]),
			indicator: "orange",
		});
	} else if (!members.length) {
		frappe.show_alert({
			message: __("{0} quotation(s) received — add the committee members to score them.", [
				quotations.length,
			]),
			indicator: "blue",
		});
	} else {
		frappe.show_alert({
			message: __("{0} quotation(s) loaded for {1} committee member(s) to score.", [
				quotations.length,
				members.length,
			]),
			indicator: "green",
		});
	}
}
