"""End-to-end check that a leave approval tells somebody at every step.

Three steps, two channels, and every one of the six combinations was silent before
leave_notifications.py existed. The failure was quiet in the way notification bugs always
are -- nothing raises, the document saves, and the only outward sign was a "Please set
default template" popup that reads like advice rather than like a notification that did not
get sent. So this walks a real application through raise, decide and cancel, and asserts on
what actually landed rather than on what was called.

Four things are worth naming:

  - IT USES THE QUEUE, LIKE PRODUCTION DOES. `enqueue_create_notification` hands the write to
    a background worker, so the check has to commit and wait rather than read its own
    transaction. Short-circuiting that would test a code path nobody runs. If the bell counts
    come back zero, look at the queue-short worker before looking at this app.

  - BOTH CHANNELS, SEPARATELY. The bell is a Notification Log; the email is hrms's own, sent
    from the Email Templates that apply_leave_notification_templates wires into HR Settings.
    They have independent failure modes -- a blank template field killed the email while the
    bell was fine, and a stopped worker does the reverse -- so neither is allowed to stand in
    as evidence for the other.

  - THE NAG POPUP IS THE REGRESSION SIGNAL. `frappe.local.message_log` is asserted empty. If
    HR Settings ever loses a template again, hrms goes back to returning early after a
    msgprint, and that popup is the first and only symptom. Catching the popup catches the
    silence.

  - NOBODY IS TOLD WHAT THEY JUST DID. The approver deciding, and the applicant applying, do
    not get notified about their own action -- checked explicitly, because
    `make_notification_logs` skips self-notification for every type EXCEPT "Alert", which is
    the type this app uses.

Creates its own employee, users and leave allocation, and deletes all of it afterwards --
including the Notification Logs, which are not children of the application and would
otherwise make a later run pass for the wrong reason. The two test logins are @example.com,
so even the emails that reach the outgoing account go nowhere. Run with

    bench --site <site> execute folt_customizations.leave_notifications_e2e.run
"""

import time

import frappe
from frappe.utils import add_days, getdate

PASS, FAIL = [], []

APPLICANT = "e2e-leave-applicant@example.com"
APPROVER = "e2e-leave-approver@example.com"
LEAVE_TYPE = "Annual Leave"
EMPLOYEE_NAME = "E2E Leave Applicant"


def check(label, condition, detail=""):
    (PASS if condition else FAIL).append(label)
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")


# --- setup / teardown ------------------------------------------------------------------------


def teardown():
    for name in frappe.get_all(
        "Leave Application", filters={"employee_name": EMPLOYEE_NAME}, pluck="name"
    ):
        doc = frappe.get_doc("Leave Application", name)
        if doc.docstatus == 1:
            doc.cancel()
        doc.delete(force=True, ignore_permissions=True)

    employee = frappe.db.get_value("Employee", {"employee_name": EMPLOYEE_NAME}, "name")
    if employee:
        for name in frappe.get_all("Leave Allocation", filters={"employee": employee}, pluck="name"):
            doc = frappe.get_doc("Leave Allocation", name)
            if doc.docstatus == 1:
                doc.cancel()
            doc.delete(force=True, ignore_permissions=True)
        # Ledger entries are written by the allocation's submit and outlive its cancellation.
        for name in frappe.get_all("Leave Ledger Entry", filters={"employee": employee}, pluck="name"):
            frappe.delete_doc("Leave Ledger Entry", name, force=True, ignore_permissions=True)
        frappe.delete_doc("Employee", employee, force=True, ignore_permissions=True)

    for user in (APPLICANT, APPROVER):
        for name in frappe.get_all("Notification Log", filters={"for_user": user}, pluck="name"):
            frappe.delete_doc("Notification Log", name, force=True, ignore_permissions=True)
        for name in frappe.get_all("Email Queue", filters={"reference_name": ["like", "HR-LAP-%"]}, pluck="name"):
            frappe.delete_doc("Email Queue", name, force=True, ignore_permissions=True)
        if frappe.db.exists("User", user):
            frappe.delete_doc("User", user, force=True, ignore_permissions=True)

    frappe.db.commit()


def make_user(email, first_name, roles):
    frappe.get_doc(
        {
            "doctype": "User",
            "email": email,
            "first_name": first_name,
            "send_welcome_email": 0,
            "user_type": "System User",
            "roles": [{"role": role} for role in roles],
        }
    ).insert(ignore_permissions=True)


def make_employee(company):
    doc = frappe.get_doc(
        {
            "doctype": "Employee",
            "first_name": EMPLOYEE_NAME,
            "employee_name": EMPLOYEE_NAME,
            "company": company,
            "status": "Active",
            "gender": frappe.get_all("Gender", pluck="name", limit=1)[0],
            "date_of_birth": "1990-01-01",
            "date_of_joining": "2020-01-01",
            "user_id": APPLICANT,
            # Named on the Employee rather than on a Department, so this test says nothing about
            # how FoLT's departments happen to be arranged today -- `Employee.leave_approver` is
            # the other of the two sources get_approvers reads.
            "leave_approver": APPROVER,
        }
    )
    doc.insert(ignore_permissions=True)
    return doc.name


def make_allocation(employee, period_start, period_end):
    doc = frappe.get_doc(
        {
            "doctype": "Leave Allocation",
            "employee": employee,
            "leave_type": LEAVE_TYPE,
            "from_date": period_start,
            "to_date": period_end,
            "new_leaves_allocated": 5,
        }
    )
    doc.insert(ignore_permissions=True)
    doc.submit()


def await_logs(name, expected, timeout=30):
    """Wait for the queue worker to write the Notification Logs, then return them.

    The worker writes in its own transaction, so ours has to be dropped each time round or we
    keep reading the snapshot we started with.
    """
    deadline = time.monotonic() + timeout
    while True:
        frappe.db.commit()
        found = frappe.get_all(
            "Notification Log",
            filters={"document_type": "Leave Application", "document_name": name},
            fields=["for_user", "subject"],
        )
        if len(found) >= expected or time.monotonic() > deadline:
            return found
        time.sleep(1)


def emails_since(count_before):
    frappe.db.commit()
    return frappe.db.count("Email Queue") - count_before


def drain_messages():
    messages = [m.get("message", "") for m in frappe.local.message_log]
    frappe.local.message_log = []
    return messages


# --- checks ----------------------------------------------------------------------------------


def run():
    print("\nLeave Application — a notification at every approval step\n")
    teardown()

    print("--- HR Settings points at the templates hrms ships ---")

    from folt_customizations.leave_notifications import LEAVE_EMAIL_TEMPLATES

    settings = frappe.get_single("HR Settings")
    check("send_leave_notification is on", bool(settings.send_leave_notification))
    for field, template in LEAVE_EMAIL_TEMPLATES.items():
        check(
            f"HR Settings.{field} is set",
            settings.get(field) == template,
            f"got {settings.get(field)!r}",
        )
        check(f"and {template!r} exists", bool(frappe.db.exists("Email Template", template)))

    print("\n--- the hooks are wired ---")

    events = frappe.get_hooks("doc_events").get("Leave Application", {})
    for event, fn in (
        ("after_insert", "notify_approver_of_new_application"),
        ("on_update", "notify_applicant_of_decision"),
        ("on_cancel", "notify_applicant_of_cancellation"),
    ):
        hooked = events.get(event) or []
        if isinstance(hooked, str):
            hooked = [hooked]
        check(
            f"Leave Application.{event} runs {fn}",
            f"folt_customizations.leave_notifications.{fn}" in hooked,
        )

    company = frappe.get_all("Company", pluck="name")[0]
    make_user(APPLICANT, "E2E Applicant", ["Employee"])
    make_user(APPROVER, "E2E Approver", ["HR User", "Leave Approver"])
    employee = make_employee(company)

    period = frappe.get_all(
        "Leave Period", filters={"is_active": 1}, fields=["from_date", "to_date"], limit=1
    )
    period_start = period[0].from_date if period else getdate("2026-01-01")
    period_end = period[0].to_date if period else getdate("2026-12-31")
    make_allocation(employee, period_start, period_end)

    # Inside the allocation, clear of both ends, and short enough not to exhaust five days.
    from_date = add_days(period_end, -10)
    to_date = add_days(from_date, 1)

    # Administrator is neither the applicant nor the approver, so both sides of every step have
    # somebody to notify. A test run as either party would pass on silence.
    frappe.set_user("Administrator")

    print("\n--- step 1: raised, and the approver is told ---")

    before = frappe.db.count("Email Queue")
    drain_messages()
    application = frappe.get_doc(
        {
            "doctype": "Leave Application",
            "employee": employee,
            "leave_type": LEAVE_TYPE,
            "from_date": from_date,
            "to_date": to_date,
            "leave_approver": APPROVER,
            "status": "Open",
        }
    )
    application.insert(ignore_permissions=True)

    nags = [m for m in drain_messages() if "Please set default template" in m]
    check("no 'Please set default template' popup on raise", not nags, "; ".join(nags)[:160])
    check("hrms emailed the approver", emails_since(before) >= 1)

    logs = await_logs(application.name, 1)
    check(
        "the approver got a bell notification",
        any(row.for_user == APPROVER for row in logs),
        f"logs: {[(r.for_user, r.subject) for r in logs]}",
    )
    check(
        "and the applicant was not told about their own application",
        not any(row.for_user == APPLICANT for row in logs),
    )

    print("\n--- step 2: approved, and the applicant is told ---")

    before = frappe.db.count("Email Queue")
    drain_messages()
    application.status = "Approved"
    application.save(ignore_permissions=True)
    application.submit()

    nags = [m for m in drain_messages() if "Please set default template" in m]
    check("no 'Please set default template' popup on approval", not nags, "; ".join(nags)[:160])
    check("hrms emailed the applicant", emails_since(before) >= 1)

    logs = await_logs(application.name, 2)
    applicant_logs = [row for row in logs if row.for_user == APPLICANT]
    check(
        "the applicant got a bell notification",
        bool(applicant_logs),
        f"logs: {[(r.for_user, r.subject) for r in logs]}",
    )
    check(
        "and it says what happened",
        any("approved" in (row.subject or "").lower() for row in applicant_logs),
        f"subjects: {[r.subject for r in applicant_logs]}",
    )
    check("the approver was not notified twice", len([r for r in logs if r.for_user == APPROVER]) == 1)

    print("\n--- step 3: cancelled, and the applicant is told again ---")

    before = frappe.db.count("Email Queue")
    drain_messages()
    application.reload()
    application.cancel()

    nags = [m for m in drain_messages() if "Please set default template" in m]
    check("no 'Please set default template' popup on cancellation", not nags, "; ".join(nags)[:160])
    check("hrms emailed the applicant", emails_since(before) >= 1)

    logs = await_logs(application.name, 3)
    cancelled = [
        row for row in logs if row.for_user == APPLICANT and "cancel" in (row.subject or "").lower()
    ]
    check(
        "the applicant was told about the cancellation",
        bool(cancelled),
        f"logs: {[(r.for_user, r.subject) for r in logs]}",
    )

    check(
        "three steps, three bell notifications, no more",
        len(logs) == 3,
        f"got {len(logs)}: {[(r.for_user, r.subject) for r in logs]}",
    )

    teardown()

    print("\n" + "=" * 60)
    print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for label in FAIL:
            print(f"  FAILED: {label}")

    return {"passed": len(PASS), "failed": len(FAIL), "failures": FAIL}
