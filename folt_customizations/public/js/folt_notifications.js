// Taking a notification off the bell.
//
// Frappe's bell can mark a notification read; it cannot get rid of one. The dropdown lists the
// reader's newest twenty Notification Logs whether they are read or not, so on a site that
// notifies nine approval workflows (notifications.notify_pending_approvers) a fortnight of read
// alerts fills every slot and the document somebody is actually waiting on drops off the bottom.
// This adds the two controls that were missing: a bin in the dropdown header that clears every
// notification the reader has already read, and an × on each read row that clears just that one.
//
// UNREAD ROWS ARE LEFT ALONE. They keep frappe's own dot, which marks them read, and the server
// refuses to delete them -- an unread notification is a task nobody has looked at yet, and the
// unread badge is a count the Desk keeps by arithmetic rather than by asking. The reasoning, and
// the "delete rather than hide" decision, are argued in notifications.clear_read_notifications.
//
// EVERYTHING HERE IS A WRAPPER, NEVER A REPLACEMENT. Every patched method calls frappe's own
// first and then adds to what it built, so a version that renders the dropdown differently
// renders it differently here too; the worst this file can do to an upgraded Desk is add a
// button with nowhere to sit. The one class it cannot reach any other way is the dropdown's
// `NotificationsView`: it is local to frappe's module and exported nowhere, so it is patched
// through the prototype of the instance frappe builds, once, the first time one is built.
//
// Patched at load rather than inside frappe.after_ajax, unlike the other two app_include_js
// files, because this needs to be in place before the sidebar constructs frappe.ui.Notifications
// rather than before a form opens. That is safe in both directions: folt_customizations is last
// in get_installed_apps(), so desk.bundle.js has already defined the class by the time this runs
// (see hooks.py), and the sidebar is not built until the boot payload has arrived.

frappe.provide("folt.notifications");

folt.notifications.CLEAR = "folt_customizations.notifications.clear_read_notifications";

// What frappe's own dropdown asks `get_notification_logs` for: the twenty rows it lists
// (NotificationsView.max_length) and the single newest row it fetches when a realtime
// notification arrives (update_dropdown). Both answers are cached by the browser -- see
// refresh_cached_list, which is the only reason this file has to know the numbers.
folt.notifications.LOG_METHOD = "frappe.desk.doctype.notification_log.notification_log.get_notification_logs";
folt.notifications.CACHED_LIMITS = [20, 1];

folt.notifications.patch = function () {
	const Notifications = frappe.ui.Notifications;
	// A frappe that renders its bell some other way, or a page that loads this without the desk
	// bundle, simply gets nothing. There is no version check here beyond the class existing:
	// every patch below is additive, so being wrong about the markup costs a button, not a bell.
	if (!Notifications || Notifications.prototype.folt_clearable) return;
	Notifications.prototype.folt_clearable = true;

	const setup_headers = Notifications.prototype.setup_headers;
	Notifications.prototype.setup_headers = function () {
		setup_headers.call(this);
		folt.notifications.add_header_action(this);
	};

	const make_tab_view = Notifications.prototype.make_tab_view;
	Notifications.prototype.make_tab_view = function (item) {
		make_tab_view.call(this, item);
		// Synchronous, and that matters: the view's constructor has already fired the fetch that
		// renders the first batch of rows, so the patch has to be in place before that answer
		// comes back. It is -- a network reply cannot land inside this call.
		if (item.id === "notifications") folt.notifications.patch_view(this.tabs[item.id]);
	};

	const mark_all_as_read = Notifications.prototype.mark_all_as_read;
	Notifications.prototype.mark_all_as_read = function (e) {
		mark_all_as_read.call(this, e);
		// Frappe marks them read in the DOM and on the server but not in the list it rendered
		// from, which is where both controls below take a row's read state from. Without this,
		// "mark all as read" leaves a bell full of rows that are read and offer no way to say so.
		const view = this.tabs && this.tabs.notifications;
		if (!view) return;
		(view.dropdown_items || []).forEach((log) => (log.read = 1));
		folt.notifications.rerender(view);
	};
};

// --- the header bin -------------------------------------------------------------------------

folt.notifications.add_header_action = function (notifications) {
	// A <button> rather than a <span> like its three neighbours: it is the one keyboard-reachable
	// route to clearing anything, since the per-row × lives inside an <a> where a button would be
	// invalid markup. Hidden until there is something to clear -- see toggle_header_action.
	const $button = $(
		`<button type="button" class="folt-clear-read hidden" aria-label="${__(
			"Clear read notifications"
		)}">${frappe.utils.icon("trash-2", "sm")}</button>`
	)
		.attr("title", __("Clear read notifications"))
		.tooltip({ delay: { show: 600, hide: 100 }, trigger: "hover" })
		.on("click", (e) => {
			// Every ancestor up to the document is watching for a click to close the dropdown on.
			e.stopImmediatePropagation();
			folt.notifications.clear_all(notifications);
		});

	// Before "mark all as read", which puts a destructive control at the far end of the row from
	// the close ×. Next to it, the two smallest targets in the header would be "dismiss this
	// dropdown" and "delete twenty records".
	const $mark_all = notifications.header_actions.find(".mark-all-read");
	if ($mark_all.length) {
		$button.insertBefore($mark_all);
	} else {
		$button.appendTo(notifications.header_actions);
	}
};

folt.notifications.clear_all = function (notifications) {
	const view = notifications.tabs && notifications.tabs.notifications;
	if (!view) return;

	// Deliberately no count in the question. The server clears every read notification the reader
	// has, and this page only ever saw the newest twenty of them -- so any number quoted here
	// would be a floor pretending to be a total. The real one is reported afterwards.
	frappe.confirm(
		__(
			"Clear every notification you have already read? They are removed for good. Unread ones stay on the bell."
		),
		() => {
			frappe.xcall(folt.notifications.CLEAR).then((cleared) => {
				// The server deleted exactly the read rows, so the same filter applied here says
				// what the dropdown now holds -- no need to re-fetch a list we can derive.
				view.dropdown_items = (view.dropdown_items || []).filter((log) => !log.read);
				folt.notifications.rerender(view);
				folt.notifications.refresh_cached_list();
				frappe.show_alert({
					message: cleared
						? __("Cleared {0} read notifications", [cleared])
						: __("Nothing to clear"),
					indicator: "green",
				});
			});
		}
	);
};

// --- the per-row × --------------------------------------------------------------------------

folt.notifications.patch_view = function (view) {
	const proto = view && Object.getPrototypeOf(view);
	if (!proto || proto.folt_clearable) return;
	proto.folt_clearable = true;

	const get_dropdown_item_html = proto.get_dropdown_item_html;
	proto.get_dropdown_item_html = function (log) {
		const $item = get_dropdown_item_html.call(this, log);
		if (log && log.read) folt.notifications.add_row_action(this, log, $item);
		return $item;
	};

	const render = proto.render_notifications_dropdown;
	proto.render_notifications_dropdown = function () {
		render.call(this);
		folt.notifications.toggle_header_action(this);
	};
};

folt.notifications.add_row_action = function (view, log, $item) {
	// Frappe renders an empty `.mark-as-read` slot on every row and only gives it a size on the
	// unread ones -- notification.scss nests that rule under `&.unread` -- so on a read row it is
	// a 0x0 box sitting in exactly the right place. This fills it rather than adding anything.
	const $slot = $item.find(".mark-as-read").first();
	if (!$slot.length) return;

	// A div, matching frappe's own unread dot, because the row is an <a> and interactive content
	// cannot nest inside one. The keyboard route to the same outcome is the header button.
	$slot
		.removeClass("mark-as-read")
		.addClass("folt-clear-notification")
		.attr({ title: __("Clear this notification"), "aria-label": __("Clear this notification") })
		.html(frappe.utils.icon("x", "sm"))
		.on("click", (e) => {
			// The row is an <a href> to the document, and it has its own click handler that closes
			// the dropdown. Both have to be stopped dead here, exactly as frappe stops them for
			// the unread dot.
			e.preventDefault();
			e.stopImmediatePropagation();
			folt.notifications.clear_one(view, log, $item);
		});
};

folt.notifications.clear_one = function (view, log, $item) {
	frappe.xcall(folt.notifications.CLEAR, { name: log.name }).then((cleared) => {
		// Nothing was deleted: the row is not this reader's, or not read after all -- which is
		// reachable, just, by clicking here in the instant between "mark all as read" repainting
		// the dropdown and its own fire-and-forget call reaching the server. Leaving the row
		// alone is then the honest answer, since it is still on the bell.
		if (!cleared) return;

		view.dropdown_items = (view.dropdown_items || []).filter((item) => item.name !== log.name);
		if (view.dropdown_items.length) {
			$item.remove();
			folt.notifications.toggle_header_action(view);
		} else {
			// The last one: re-render, so the reader gets frappe's empty state rather than a
			// dropdown with nothing but a "See all Activity" footer in it.
			folt.notifications.rerender(view);
		}
		folt.notifications.refresh_cached_list();
	});
};

// --- keeping the dropdown honest ------------------------------------------------------------

folt.notifications.rerender = function (view) {
	// The container is emptied here rather than left to frappe, which empties it only on the
	// branch that has rows to draw: `render_notifications_dropdown` appends its empty state
	// without clearing first, so re-rendering an emptied list would otherwise leave every stale
	// row on screen with "No new notifications" underneath.
	view.container.empty();
	view.render_notifications_dropdown();
};

folt.notifications.toggle_header_action = function (view) {
	// Shown when the bell is holding something read, which is the same question as "would the bin
	// do anything visible". A reader with nothing but unread rows on the bell may still have read
	// ones further back in the log; those are reached from "See all Activity", not from here.
	const has_read = (view.dropdown_items || []).some((log) => log.read);
	view.parent.find(".folt-clear-read").toggleClass("hidden", !has_read);
};

folt.notifications.refresh_cached_list = function () {
	// `get_notification_logs` is decorated with frappe's @http_cache -- private, max-age 60,
	// stale-while-revalidate an hour -- and the Desk asks for it with jQuery `cache: true`, so
	// the browser keeps the answer and will serve it again. Deleting the rows server-side does
	// not touch that copy: reload the Desk within the minute and the notifications just cleared
	// are all back, only to disappear again on the reload after. Re-requesting the same two URLs
	// with `cache: "reload"` replaces the stored response with the truth.
	//
	// Fire and forget, and failure is silent: this is a cache repair, and the delete it follows
	// has already succeeded.
	folt.notifications.CACHED_LIMITS.forEach((limit) => {
		fetch(`/api/method/${folt.notifications.LOG_METHOD}?limit=${limit}`, {
			cache: "reload",
			credentials: "same-origin",
			headers: { Accept: "application/json" },
		}).catch(() => null);
	});
};

folt.notifications.patch();
