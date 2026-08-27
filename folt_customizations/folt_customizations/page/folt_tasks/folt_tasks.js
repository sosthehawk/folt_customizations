// My Tasks: everything waiting for me, across every FoLT chain, grouped by what is being asked.
//
// The server half is folt_tasks.py, and it derives the queues from the active workflows rather
// than from anything configured here -- so this file names no doctype and no state, and a chain
// added next month appears with nothing added to it.
//
// A desk Page rather than a portal page, for three reasons that all come down to it being free:
// all eight FoLT roles are System Users, so the session, the roles and the permission filtering
// are already in place; `Page.load_assets` reads this file off disk on every request, so there is
// no build step and no `bench build` in the deploy loop; and every row can route straight into
// the document with frappe.set_route, which is the entire point of a task list.

frappe.provide("folt.tasks");

folt.tasks.FETCH = "folt_customizations.folt_customizations.page.folt_tasks.folt_tasks.my_tasks";

folt.tasks.BUCKETS = [
	{ key: "awaiting", label: __("My Tasks"), empty: __("Nothing is waiting for you.") },
	{ key: "drafts", label: __("Drafts"), empty: __("You have no unfinished documents.") },
	{
		key: "approved",
		label: __("Approved"),
		empty: __("Nothing you have raised or approved is in flight."),
	},
	{
		key: "archives",
		label: __("Archives"),
		empty: __("Nothing of yours has been closed or turned down yet."),
	},
];

frappe.pages["folt-tasks"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("My Tasks"),
		single_column: true,
	});

	// The CSS in folt_desk.css is all nested under body.folt-guided. folt_guide.js sets it on
	// boot, but this page can be the very first thing rendered after a login, so it is set here
	// too rather than depending on the order the two files happen to run in.
	document.body.classList.add("folt-guided");

	const state = { bucket: "awaiting", counts: {} };

	const $layout = $(`
		<div class="folt-tasks">
			<div class="folt-tasks-nav"></div>
			<div class="folt-tasks-body"><div class="text-muted">${__("Loading…")}</div></div>
		</div>
	`).appendTo(page.main);

	page.set_secondary_action(__("Refresh"), () => load());

	function load(bucket) {
		if (bucket) state.bucket = bucket;

		frappe
			.xcall(folt.tasks.FETCH, { bucket: state.bucket })
			.then((result) => {
				// Counts for the two backward-looking buckets are only computed when one of them
				// is being viewed -- they cost two extra queries per doctype. So the previous
				// counts are kept rather than being overwritten with a zero that is not a zero.
				state.counts = Object.assign({}, state.counts, folt.tasks.real_counts(result));
				folt.tasks.render_nav($layout.find(".folt-tasks-nav"), state, load);
				folt.tasks.render_body($layout.find(".folt-tasks-body"), result);
			})
			.catch(() => {
				$layout
					.find(".folt-tasks-body")
					.html(`<div class="text-muted">${__("Could not load your tasks.")}</div>`);
			});
	}

	load();
};

// A count of 0 for a bucket the server did not compute is absence, not emptiness. Only the
// bucket that was actually asked for, plus the two cheap ones, carry a trustworthy number.
folt.tasks.real_counts = function (result) {
	const trustworthy = { awaiting: true, drafts: true, [result.bucket]: true };
	const counts = {};
	Object.keys(result.counts || {}).forEach((key) => {
		if (trustworthy[key]) counts[key] = result.counts[key];
	});
	return counts;
};

folt.tasks.render_nav = function ($nav, state, load) {
	const items = folt.tasks.BUCKETS.map((bucket) => {
		const count = state.counts[bucket.key];
		const badge =
			count === undefined
				? ""
				: `<span class="folt-tasks-count">${count}</span>`;
		const active = bucket.key === state.bucket ? " is-active" : "";

		return `<button class="folt-tasks-tab${active}" data-bucket="${bucket.key}">
			<span>${bucket.label}</span>${badge}
		</button>`;
	}).join("");

	$nav.html(items);
	$nav.find("[data-bucket]").on("click", function () {
		load($(this).attr("data-bucket"));
	});
};

folt.tasks.render_body = function ($body, result) {
	const groups = result.groups || [];

	if (!groups.length) {
		const bucket = folt.tasks.BUCKETS.find((b) => b.key === result.bucket);
		$body.html(
			`<div class="folt-tasks-empty">${(bucket && bucket.empty) || __("Nothing here.")}</div>`
		);
		return;
	}

	$body.html(groups.map(folt.tasks.group_html).join(""));

	$body.find("[data-open-doctype]").on("click", function () {
		frappe.set_route(
			"Form",
			$(this).attr("data-open-doctype"),
			$(this).attr("data-open-name")
		);
	});
};

folt.tasks.group_html = function (group) {
	// The heading is what is being asked, not which doctype it lives in: "Employee Advance —
	// Checked (step 2 of 6)" reads as a job, and three of them read as one job to do three times.
	const step =
		group.lane === null || group.lane === undefined
			? ""
			: `<span class="folt-tasks-group-step">${__("step {0} of {1}", [
					group.lane + 1,
					group.of,
				])}</span>`;

	const rows = group.rows.map(folt.tasks.row_html).join("");

	return `<section class="folt-tasks-group">
		<div class="folt-tasks-group-head">
			<span class="folt-tasks-group-title">${__(group.doctype)} &middot; ${__(group.step_label)}</span>
			${step}
			<span class="folt-tasks-count">${group.rows.length}</span>
		</div>
		<ul class="folt-tasks-rows">${rows}</ul>
	</section>`;
};

folt.tasks.row_html = function (row) {
	const amount =
		row.amount === null || row.amount === undefined
			? ""
			: `<span class="folt-tasks-amount">${format_currency(row.amount)}</span>`;

	// How long it has been sitting is the reason to look at one row rather than another, so it
	// is on every row and emphasised once it stops being reasonable.
	const age =
		row.age_days > 0
			? `<span class="folt-tasks-age${row.age_days >= 7 ? " is-stale" : ""}">${__(
					"{0}d",
					[row.age_days]
				)}</span>`
			: `<span class="folt-tasks-age">${__("today")}</span>`;

	const title = frappe.utils.escape_html(row.title || row.name);
	const who = frappe.utils.escape_html(row.owner_name || "");

	return `<li class="folt-tasks-row" data-open-doctype="${frappe.utils.escape_html(
		row.doctype
	)}" data-open-name="${frappe.utils.escape_html(row.name)}">
		<span class="folt-tasks-row-main">
			<span class="folt-tasks-row-title">${title}</span>
			<span class="folt-tasks-row-meta">${frappe.utils.escape_html(row.name)}${
				who ? " &middot; " + __("raised by {0}", [who]) : ""
			}</span>
		</span>
		${amount}
		${age}
	</li>`;
};
