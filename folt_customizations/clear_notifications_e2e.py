"""End-to-end check that clearing the bell clears exactly what it should.

`notifications.clear_read_notifications` is four lines with no branches, and all four of the
things that can go wrong with it are about the filter rather than about the logic: it deletes
rows, it runs as a whitelisted method any logged-in user can call, and the `for_user` clause IS
its permission check -- there is no `has_permission`, no `frappe.only_for` and no ptype behind it
(see the docstring for why that is the right shape). So what is worth asserting is the shape of
the filter, from the caller's side, against real rows:

  - a caller clears their OWN read notifications and nobody else's, whether they clear all of
    them or name one -- including when the name they pass belongs to somebody else's bell, which
    is the one call that a permission bug would make succeed;
  - an UNREAD notification survives every one of those calls, because the Desk's unread badge is
    a count kept by arithmetic and a row deleted from under it leaves the bell claiming work
    that no longer exists;
  - the return value is the number actually deleted, since that is what the reader is told.

Rows are inserted directly rather than raised through a workflow: what is under test is the
delete, and notify_pending_approvers already has the chain that produces these rows covered.
They are inserted as type "Alert", which is what this app sends and which frappe's
`notification_skip_email_types` hook keeps out of the mail queue.

Creates its own two users and deletes them, and their notifications, afterwards -- both are
@example.com so nothing leaves the site even if a row were mailed. Run with

    bench --site <site> execute folt_customizations.clear_notifications_e2e.run
"""

import frappe

from folt_customizations.notifications import clear_read_notifications

PASS, FAIL = [], []

OWNER = "e2e-bell-owner@example.com"
OTHER = "e2e-bell-other@example.com"


def check(label, condition, detail=""):
    (PASS if condition else FAIL).append(label)
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")


def make_user(email):
    if frappe.db.exists("User", email):
        return email
    user = frappe.get_doc(
        {
            "doctype": "User",
            "email": email,
            "first_name": email.split("@")[0],
            "send_welcome_email": 0,
            "roles": [{"role": "Employee"}],
        }
    )
    user.flags.no_welcome_mail = True
    user.insert(ignore_permissions=True)
    return email


def make_log(for_user, subject, read):
    log = frappe.get_doc(
        {
            "doctype": "Notification Log",
            "type": "Alert",
            "for_user": for_user,
            "subject": subject,
            "email_content": subject,
            "read": read,
        }
    ).insert(ignore_permissions=True)
    return log.name


def surviving(for_user):
    return set(frappe.get_all("Notification Log", filters={"for_user": for_user}, pluck="name"))


def as_user(user, name=None):
    """Call the endpoint the way the Desk does: as the signed-in user, nothing else passed."""
    frappe.set_user(user)
    try:
        return clear_read_notifications(name) if name else clear_read_notifications()
    finally:
        frappe.set_user("Administrator")


def run():
    print("\nClearing the bell — one caller's read notifications, and nobody else's\n")
    frappe.set_user("Administrator")
    make_user(OWNER)
    make_user(OTHER)

    try:
        first = make_log(OWNER, "E2E read 1", read=1)
        second = make_log(OWNER, "E2E read 2", read=1)
        unread = make_log(OWNER, "E2E unread", read=0)
        theirs = make_log(OTHER, "E2E somebody else's, read", read=1)
        frappe.db.commit()

        # 1. Clearing one by name.
        cleared = as_user(OWNER, first)
        check("naming one read notification clears one", cleared == 1, f"returned {cleared}")
        left = surviving(OWNER)
        check("...and only that one", left == {second, unread}, f"left: {sorted(left)}")

        # 2. Naming somebody else's row. This is the call that matters: the endpoint takes a
        #    docname straight off the wire, so a filter that trusted it would delete OTHER's bell
        #    on OWNER's say-so. It has to come back 0, not raise -- a refusal would confirm the
        #    row exists.
        cleared = as_user(OWNER, theirs)
        check("naming another user's notification clears nothing", cleared == 0, f"returned {cleared}")
        check("...and leaves it on their bell", surviving(OTHER) == {theirs})

        # 3. Naming an unread row of one's own.
        cleared = as_user(OWNER, unread)
        check("naming an unread notification clears nothing", cleared == 0, f"returned {cleared}")
        check("...and leaves it on the bell", unread in surviving(OWNER))

        # 4. Clearing the lot.
        cleared = as_user(OWNER)
        check("clearing all read notifications clears the rest", cleared == 1, f"returned {cleared}")
        left = surviving(OWNER)
        check("...leaves the unread one behind", left == {unread}, f"left: {sorted(left)}")
        check("...and does not touch another user's bell", surviving(OTHER) == {theirs})

        # 5. Nothing to clear. The Desk hides the control in this state, but the endpoint is
        #    whitelisted and a second click on a stale dropdown has to be harmless.
        cleared = as_user(OWNER)
        check("clearing again clears nothing and does not fail", cleared == 0, f"returned {cleared}")
        check("...with the unread one still there", surviving(OWNER) == {unread})

    finally:
        frappe.set_user("Administrator")
        for user in (OWNER, OTHER):
            frappe.db.delete("Notification Log", {"for_user": user})
            if frappe.db.exists("User", user):
                frappe.delete_doc("User", user, force=True, ignore_permissions=True)
        frappe.db.commit()

    check_desk_contract()

    print(f"\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED: " + "; ".join(FAIL))
    return {"passed": len(PASS), "failed": len(FAIL), "failures": FAIL}


# --- the half that has no browser ------------------------------------------------------------
# folt_notifications.js wraps five of frappe's own methods and fills a slot in markup frappe
# renders. All of that fails SILENTLY: an upgrade that renames a method leaves a bell that simply
# never grows the controls, with a clean console, which is the least debuggable failure available
# (the same reasoning as folt_guide.js's own comment about catching too much).
#
# There is no browser here to click the thing, so this asserts the contract instead: every name
# the patch reaches into is still in the frappe it will be loaded next to. It reads frappe's
# source off disk, the way theme_e2e.py reads the stylesheets, so it is checking the code that
# will actually run rather than a copy of it.

# name in frappe's notifications.js -> what folt_notifications.js does with it
PATCHED = {
    "frappe.ui.Notifications = class": "the class the patch hangs off",
    "setup_headers()": "wrapped, to add the bin to the dropdown header",
    "make_tab_view(item)": "wrapped, to reach the NotificationsView prototype",
    "mark_all_as_read(e)": "wrapped, so marking all read leaves rows that can be cleared",
    "get_dropdown_item_html(notification_log)": "wrapped, to fill the slot on a read row",
    "render_notifications_dropdown()": "wrapped, to show or hide the bin",
    "this.header_actions": "where the bin is inserted",
    '"mark-all-read"': "the sibling the bin is inserted before",
    '"mark-as-read"': "the empty slot on a read row that becomes the x",
    "this.dropdown_items": "the rendered list, filtered in place after a clear",
    "this.max_length = 20": "the limit whose cached answer has to be refreshed",
    "get_notifications_list(1)": "the second cached limit, fetched when a notification arrives",
    "cache: true": "why the browser keeps the answer at all",
}


def check_desk_contract():
    print("")
    dropdown = frappe.get_app_path(
        "frappe", "public", "js", "frappe", "ui", "notifications", "notifications.js"
    )
    with open(dropdown, encoding="utf-8") as handle:
        source = handle.read()

    missing = {name: why for name, why in PATCHED.items() if name not in source}
    check(
        "frappe's notification dropdown still has everything the patch wraps",
        not missing,
        "; ".join(f"{name} ({why})" for name, why in missing.items())
        or f"{len(PATCHED)} names",
    )

    log = frappe.get_app_path("frappe", "desk", "doctype", "notification_log", "notification_log.py")
    with open(log, encoding="utf-8") as handle:
        server = handle.read()

    # The cache-busting fetch exists only because of this decorator. If frappe drops it, the
    # fetch is dead weight and the comment explaining it is wrong.
    check(
        "the notification list is still http_cached, so the cache repair is still needed",
        "@http_cache(max_age=60" in server,
        "" if "@http_cache(max_age=60" in server else "no longer cached -- drop refresh_cached_list",
    )
    # And the list still ignores `read`, which is the whole reason a read notification needs
    # clearing rather than just marking.
    listing = server.split("def get_notification_logs")[-1].split("def ")[0]
    check(
        "the dropdown still lists read notifications alongside unread ones",
        "read" not in listing,
        "" if "read" not in listing else "frappe now filters them itself",
    )
