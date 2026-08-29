// The supplier-facing pricing form on /rfq/<name>. Loaded by templates/pages/rfq.html.
//
// erpnext's equivalent (templates/includes/rfq.js) ships the whole RFQ document to the browser
// as `window.doc` and posts it back as the quotation, so every field of the bid is whatever the
// page says it is. Here the browser only ever sends what the supplier actually typed -- a qty
// and a rate per RFQ line -- and the server rebuilds the rest from the RFQ (see
// supplier_portal.save_quotation). The totals below are for the person reading the page;
// the ones that count are recomputed by ERPNext on save.

$(document).ready(function () {
	const rfq = {{ folt_rfq }};
	const lines = {};
	rfq.items.forEach((item) => (lines[item.idx] = item));

	function render_totals() {
		let grand_total = 0;
		Object.values(lines).forEach((line) => {
			const amount = flt(line.qty) * flt(line.rate);
			grand_total += amount;
			$(`.rfq-amount[data-idx='${line.idx}']`).text(format_number(amount, rfq.number_format, 2));
		});
		$(".tax-grand-total").text(format_number(grand_total, rfq.number_format, 2));
	}

	// Read straight back out of the field on every change rather than trusting the keystroke:
	// the value is re-formatted in place, so what the supplier sees and what is posted stay the
	// same number even when they type "12,500" or leave the field empty.
	function bind(selector, field) {
		$(".rfq-items").on("change", selector, function () {
			const line = lines[$(this).attr("data-idx")];
			if (!line) return;
			line[field] = flt($(this).val());
			$(this).val(format_number(line[field], rfq.number_format, 2));
			render_totals();
		});
	}

	// The server renders the inputs with a plain "%.2f", while every edit below rewrites them
	// through format_number -- so an untouched field read "15000.00" and the same field read
	// "15,000.00" the moment it was touched. Painting them once on load makes the page's
	// starting state the same state it reaches after one keystroke, using the site's own
	// number format rather than the template's.
	function paint_inputs() {
		Object.values(lines).forEach((line) => {
			$(`.rfq-qty[data-idx='${line.idx}']`).val(format_number(line.qty, rfq.number_format, 2));
			$(`.rfq-rate[data-idx='${line.idx}']`).val(format_number(line.rate, rfq.number_format, 2));
		});
	}

	$("input").on("focus", function () {
		$(this).select();
	});
	bind(".rfq-qty", "qty");
	bind(".rfq-rate", "rate");
	paint_inputs();
	render_totals();

	$(".folt-save-quotation").on("click", function () {
		frappe.freeze();
		frappe.call({
			type: "POST",
			method: "folt_customizations.supplier_portal.save_quotation",
			btn: this,
			args: {
				request_for_quotation: rfq.rfq,
				items: Object.values(lines),
				terms: $(".terms-feedback").val(),
			},
			callback: function (r) {
				frappe.unfreeze();
				if (r.message) {
					// Back to the quotation itself: it is the receipt that the price reached
					// FoLT, and reloading this page would only show the same form again.
					window.location.href = "/supplier-quotations/" + encodeURIComponent(r.message);
				}
			},
			error: function () {
				frappe.unfreeze();
			},
		});
	});
});
