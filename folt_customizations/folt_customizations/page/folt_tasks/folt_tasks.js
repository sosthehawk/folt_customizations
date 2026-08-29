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
// the document, which is the entire point of a task list.
//
// ROWS ARE <a href>, NOT DIVS WITH A CLICK HANDLER, and that is load-bearing rather than tidy.
// frappe's router installs a delegated `$("body").on("click", "a")` handler which turns any
// `/desk/...` href into frappe.set_route -- so an anchor gives byte-for-byte the same SPA
// navigation the old click handler did, and additionally gives Tab, Enter, middle-click and
// cmd-click, none of which worked before. Two consequences worth knowing:
//   - The old delegated click handler had to GO, not merely be left alone. router.js only bails
//     out when the element carries an `onclick` ATTRIBUTE; a jQuery handler is invisible to it,
//     so keeping both would navigate twice.
//   - frappe sets `outline: 0` on every anchor state globally (desk/global.scss) and underlines
//     `a:hover`. folt_desk.css answers both for .folt-tasks-row. Without the first, keyboard
//     focus on a row would be completely invisible.

frappe.provide("folt.tasks");

folt.tasks.FETCH = "folt_customizations.folt_customizations.page.folt_tasks.folt_tasks.my_tasks";

// `empty_head` and `tone` are the empty state; `empty` is the sentence underneath and is
// unchanged. The tone picks a tick or a dash: "nothing is waiting for you" is an achievement and
// gets a tick, "you have not filed anything yet" is not and gets a dash.
folt.tasks.BUCKETS = [
	{
		key: "awaiting",
		label: __("My Tasks"),
		empty_head: __("All clear"),
		tone: "is-green",
		empty: __("Nothing is waiting for you."),
	},
	{
		key: "drafts",
		label: __("Drafts"),
		empty_head: __("Nothing unfinished"),
		tone: "is-dash",
		empty: __("You have no unfinished documents."),
	},
	{
		key: "approved",
		label: __("Approved"),
		empty_head: __("Nothing in flight"),
		tone: "is-blue",
		empty: __("Nothing you have raised or approved is in flight."),
	},
	{
		key: "archives",
		label: __("Archives"),
		empty_head: __("Nothing closed yet"),
		tone: "is-dash",
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

	// aria-live on the body is what makes switching buckets ANNOUNCED. Today the content is
	// swapped silently while focus stays on the tab, so a screen-reader user gets no signal that
	// anything happened. role="region" needs a name to mean anything, so render_body sets
	// aria-label from the active bucket.
	const $layout = $(`
		<div class="folt-tasks">
			<nav class="folt-tasks-nav" aria-label="${__("Task buckets")}"></nav>
			<div class="folt-tasks-body" role="region" aria-busy="true"
				aria-live="polite" aria-label="${__("Tasks")}"></div>
		</div>
	`).appendTo(page.main);

	page.set_secondary_action(__("Refresh"), () => load());

	function load(bucket) {
		if (bucket) state.bucket = bucket;

		const $body = $layout.find(".folt-tasks-body");
		const first = !$layout.find(".folt-tasks-tab").length;

		// First paint gets a skeleton shaped like the rows it is standing in for. A bucket
		// switch instead keeps the old rows on screen and dims them, so nothing jumps and
		// nothing flashes -- the content is about to be replaced, not absent.
		if (first) $body.html(folt.tasks.skeleton_html());
		else $body.addClass("is-loading");
		$body.attr("aria-busy", "true");

		// The nav is drawn from `state` BEFORE the call, not only after it. A first-load failure
		// used to leave a page with no bucket buttons at all and therefore no way out of the
		// error. Safe with respect to real_counts: state.counts starts empty, so every badge is
		// undefined and none renders -- absence still reads as absence.
		folt.tasks.render_nav($layout.find(".folt-tasks-nav"), state, load);

		frappe.xcall(folt.tasks.FETCH, { bucket: state.bucket }).then(
			(result) => {
				// Counts for the two backward-looking buckets are only computed when one of them
				// is being viewed -- they cost two extra queries per doctype. So the previous
				// counts are kept rather than being overwritten with a zero that is not a zero.
				state.counts = Object.assign({}, state.counts, folt.tasks.real_counts(result));
				folt.tasks.render_nav($layout.find(".folt-tasks-nav"), state, load);
				folt.tasks.render_body($body, result);
				$body.removeClass("is-loading").attr("aria-busy", "false");
			},
			// A two-argument .then, NOT .catch -- and this is the same trap folt_guide.js spends
			// five lines warning about. A .catch here also swallows every rendering bug and
			// reports it as "could not load your tasks": a TypeError in group_html would surface
			// as a server error with a clean console, which is the least debuggable failure
			// available. This handler covers the fetch and nothing else.
			(error) => {
				folt.tasks.render_error($body, load);
				$body.removeClass("is-loading").attr("aria-busy", "false");
				console.error("folt.tasks: could not load", error);
			}
		);
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

// Plain buttons with aria-current, deliberately NOT role="tab". A correct ARIA tablist owes
// arrow-key navigation with a roving tabindex; a half-implemented one is worse than none,
// because it promises arrow keys and then swallows Tab. These are four filtered views inside a
// labelled <nav>, natural Tab traversal already works, and the aria-live body does the
// announcing.
folt.tasks.render_nav = function ($nav, state, load) {
	const items = folt.tasks.BUCKETS.map((bucket) => {
		const count = state.counts[bucket.key];
		// UNCHANGED CONTRACT: undefined means the server did not compute it. No badge, no
		// placeholder, and above all no reserved slot that could be mistaken for a zero.
		const badge =
			count === undefined
				? ""
				: `<span class="folt-tasks-count">${count}<span class="folt-visually-hidden"> ${__(
						"waiting"
					)}</span></span>`;
		const active = bucket.key === state.bucket;

		// type="button": these sit outside a form today, but a bare <button> inside a Page is a
		// latent submit the day one wraps them.
		return `<button type="button" class="folt-tasks-tab${active ? " is-active" : ""}"
				data-bucket="${bucket.key}"${active ? ' aria-current="true"' : ""}>
			<span>${bucket.label}</span>${badge}
		</button>`;
	}).join("");

	$nav.html(items);
	$nav.find("[data-bucket]").on("click", function () {
		load($(this).attr("data-bucket"));
	});
};

folt.tasks.render_body = function ($body, result) {
	const bucket = folt.tasks.BUCKETS.find((b) => b.key === result.bucket);
	$body.attr("aria-label", (bucket && bucket.label) || __("Tasks"));

	const groups = result.groups || [];

	if (!groups.length) {
		$body.html(folt.tasks.empty_html(bucket));
		return;
	}

	// Wrapped, so the space between cards is one `gap` in CSS rather than a margin on each card
	// that the last one also gets.
	$body.html(
		`<div class="folt-tasks-list">${groups
			.map((group) => folt.tasks.group_html(group, result.bucket))
			.join("")}</div>`
	);

	// No click handler is bound here on purpose. See the header: the rows are anchors, and
	// frappe's own delegated handler routes them.
};

folt.tasks.group_html = function (group, bucket) {
	// The heading is what is being asked, not merely which doctype it lives in: "Employee Advance
	// / Checked / step 2 of 6" reads as a job, and three of them read as one job to do three
	// times. The doctype leads because when you are holding five steps across five chains, which
	// chain it is is the scan key.
	//
	// lane === null is workflow_shape.locate saying the state is off the happy path -- the
	// document was sent back. It is already how this function decided whether to print "step N of
	// M"; naming it makes it available to the card as well.
	const off_path = group.lane === null || group.lane === undefined;

	const step = off_path
		? ""
		: `<span>${__("step {0} of {1}", [group.lane + 1, group.of])}</span>
			<span class="folt-tasks-progress" aria-hidden="true">${Array.from(
				{ length: group.of },
				(_, i) =>
					`<span class="folt-tasks-progress-dot ${
						i < group.lane ? "is-done" : i === group.lane ? "is-current" : ""
					}"></span>`
			).join("")}</span>`;

	// group.waiting_on has always been on the wire and has never been rendered. In the `awaiting`
	// bucket it would only ever say "you", so it earns its space in the other three.
	const with_whom =
		bucket !== "awaiting" && (group.waiting_on || []).length
			? `<span class="folt-tasks-with">${__("with {0}", [
					group.waiting_on.map((role) => __(role)).join(__(" or ")),
				])}</span>`
			: "";

	const rows = group.rows.map((row) => folt.tasks.row_html(row, off_path)).join("");

	// An <h2>: the page had no heading structure at all below the page title, so a screen-reader
	// user could not jump between groups.
	return `<section class="folt-tasks-group${off_path ? " is-off-path" : ""}">
		<div class="folt-tasks-group-head">
			<h2 class="folt-tasks-group-title">
				<span>${__(group.doctype)}</span>
				<span class="folt-tasks-group-sub">
					<span>${__(group.step_label)}</span>${step}
				</span>
			</h2>
			${with_whom}
			<span class="folt-tasks-count">${group.rows.length}<span class="folt-visually-hidden"> ${__(
				"documents"
			)}</span></span>
		</div>
		<ul class="folt-tasks-rows">${rows}</ul>
	</section>`;
};

folt.tasks.row_html = function (row, off_path) {
	const amount =
		row.amount === null || row.amount === undefined
			? ""
			: `<span class="folt-tasks-amount">${format_currency(row.amount)}</span>`;

	// row.modified has always been on the wire and never used. It turns a terse "12d" into
	// something with a real answer behind it, at no server cost.
	const when = row.modified ? frappe.datetime.str_to_user(row.modified) : "";
	const title_attr = when ? ` title="${frappe.utils.escape_html(when)}"` : "";

	// How long it has been sitting is the reason to look at one row rather than another, so it is
	// on every row and emphasised once it stops being reasonable.
	const age =
		row.age_days > 0
			? `<span class="folt-tasks-age${
					row.age_days >= 7 ? " is-stale" : ""
				}"${title_attr}>${__("{0}d", [row.age_days])}<span class="folt-visually-hidden"> ${__(
					"days waiting"
				)}</span></span>`
			: `<span class="folt-tasks-age"${title_attr}>${__("today")}</span>`;

	const title = frappe.utils.escape_html(row.title || row.name);
	const who = frappe.utils.escape_html(row.owner_name || "");

	// Round-trips safely: get_form_link already encodeURIComponent's the name, and the handful of
	// characters escape_html adds on top (& " ' <) are decoded by the HTML parser back to
	// themselves before the URL is ever parsed.
	const href = frappe.utils.escape_html(frappe.utils.get_form_link(row.doctype, row.name));

	// In an off-path group the heading's step_label came from whichever row _group happened to
	// see first, and one lane can hold more than one off-path state -- so the row says its own.
	const state = off_path && row.state ? `<span>${__(row.state)}</span>` : "";

	// No aria-label on the anchor. The natural content already reads well ("link, Kalokol
	// community forum float, HR-EAD-2026-00041, raised by Grace Ekiru, KES 84,500, 12d, days
	// waiting"), and an aria-label would break the accessible-name-contains-visible-label match
	// that voice-control users rely on. Context goes in visually-hidden spans instead.
	//
	// The separators between the meta spans are a CSS ::before, not a string concatenated here.
	return `<li>
		<a class="folt-tasks-row" href="${href}">
			<span class="folt-tasks-row-main">
				<span class="folt-tasks-row-title">${title}</span>
				<span class="folt-tasks-row-meta">
					<span class="folt-tasks-row-id">${frappe.utils.escape_html(row.name)}</span>
					${who ? `<span>${__("raised by {0}", [who])}</span>` : ""}
					${state}
				</span>
			</span>
			${amount}
			${age}
		</a>
	</li>`;
};

// Geometry mirrors .folt-tasks-row, so when the real rows arrive nothing moves.
// aria-hidden because a live region full of empty boxes is noise; the visually-hidden sentence
// is what actually gets announced.
folt.tasks.skeleton_html = function (groups = 2, rows = 3) {
	const row = `<div class="folt-skel-row">
			<div class="folt-skel-main">
				<div class="folt-skel folt-skel-title"></div>
				<div class="folt-skel folt-skel-meta"></div>
			</div>
			<div class="folt-skel folt-skel-amount"></div>
			<div class="folt-skel folt-skel-age"></div>
		</div>`;

	const card = `<section class="folt-tasks-group">
			<div class="folt-tasks-group-head"><div class="folt-skel folt-skel-head"></div></div>
			<div class="folt-skel-rows">${row.repeat(rows)}</div>
		</section>`;

	return `<div class="folt-tasks-list" aria-hidden="true">${card.repeat(groups)}</div>
		<span class="folt-visually-hidden">${__("Loading your tasks…")}</span>`;
};

folt.tasks.empty_html = function (bucket) {
	return `<div class="folt-tasks-empty">
		<div class="folt-tasks-empty-mark ${(bucket && bucket.tone) || ""}" aria-hidden="true"></div>
		<p class="folt-tasks-empty-head">${(bucket && bucket.empty_head) || __("Nothing here")}</p>
		<p class="folt-tasks-empty-body">${(bucket && bucket.empty) || __("Nothing here.")}</p>
	</div>`;
};

// role="alert" so the failure is announced. The retry button is the part that was missing: the
// old error path replaced the body with one grey sentence and left no way to try again.
// The reassurance is deliberate -- this page only reads documents, and a reader who has just
// submitted something has no way of knowing that.
folt.tasks.render_error = function ($body, load) {
	$body.html(`<div class="folt-tasks-error" role="alert">
		<p class="folt-tasks-error-head">${__("Your tasks could not be loaded.")}</p>
		<p class="folt-tasks-error-body">${__(
			"The server did not answer. Nothing has happened to your documents — this page only reads them."
		)}</p>
		<button type="button" class="btn btn-default btn-sm" data-folt-retry>${__(
			"Try again"
		)}</button>
	</div>`);

	$body.find("[data-folt-retry]").on("click", () => load());
};
