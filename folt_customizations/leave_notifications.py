"""Tell somebody at every step of a leave approval -- in the Desk, and by email.

A Leave Application passes through three moments where a named person is waiting to hear:
it is raised and the approver has to decide, the decision is made and the applicant has to
be told, and an approved leave is later cancelled and the applicant has to be told again.
None of the three reached a FoLT user in the Desk, for three separate reasons:

  - EMAIL WAS WIRED BUT DEAD. hrms gates both of its leave emails on an Email Template named
    in HR Settings -- `leave_approval_notification_template` and
    `leave_status_notification_template` -- and neither was set. `notify_leave_approver` and
    `notify_employee` then take an early return after a `frappe.msgprint`, so instead of a
    notification the person saving the form got a nag popup telling them to go and configure
    something. The two templates hrms ships were on the site the whole time, unreferenced.
    `apply_leave_notification_templates` below points the settings at them -- which is now
    only the fallback path, because leave_email.py owns the body of all three emails and so
    no longer depends on a template being named at all. It still matters for the site whose
    administrator has chosen a template of their own, which that module lets win.

  - THE BELL NEVER RANG. FoLT's bell notifications hang off `Workflow Action.after_insert`
    (notifications.notify_pending_approvers), which covers all nine FoLT workflows at once
    and, for exactly that reason, covers nothing without a workflow. Leave Application has
    none -- it uses hrms's own `status` + `leave_approver` approval -- so it fell through the
    one gap in that design. Hence the three hooks below, which name the doctype explicitly.

  - WHAT DID FIRE WAS INVISIBLE. `PWANotificationsMixin` writes `PWA Notification` rows on
    insert and on the status change. Those are read by the Frappe HR mobile app and by
    nothing else; nobody working in the Desk has ever seen one. They are left alone here --
    they are not wrong, just not the whole story.

Email and bell divide the work the same way notifications.py already divides it for the
committee: the bell needs no mail server and always lands, the email carries the detail to
somebody who is not looking at the Desk. They do not double up. What that email looks like --
the FoLT masthead, and a body that says which of the three things has happened -- is
leave_email.py; this module is the bell and the settings. Notification type "Alert" is
listed in frappe's `notification_skip_email_types` hook, so a Notification Log of that type
never mails itself -- the only email is hrms's own, from the templates wired up below.

`HR Settings.send_leave_notification` stays the single switch over both channels. It already
gates hrms's emails; the bell is gated on it too, so that turning leave notifications off
means silence rather than half-silence.
"""

import frappe
from frappe import _
from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
from frappe.utils import get_url_to_form

# One definition of "is this login worth notifying", shared with the workflow notifications.
from folt_customizations.notifications import _active_users

# The Email Templates hrms ships with the app but never points HR Settings at.
LEAVE_EMAIL_TEMPLATES = {
    "leave_approval_notification_template": "Leave Approval Notification",
    "leave_status_notification_template": "Leave Status Notification",
}

DECIDED = ("Approved", "Rejected")


def apply_leave_notification_templates():
    """Point HR Settings at the leave Email Templates, if nothing else is chosen.

    Runs after_install and after_migrate, alongside the other settings this app owns.

    Only fills a BLANK field. These are a default, not a policy: an administrator who picks a
    different template has made a decision, and re-imposing ours on the next migrate would
    silently undo it. A blank field, by contrast, is not a decision -- it is the state that
    produces the "Please set default template" popup instead of a notification.
    """
    settings = frappe.get_single("HR Settings")
    changed = []

    for field, template in LEAVE_EMAIL_TEMPLATES.items():
        if settings.get(field):
            continue
        # A site that has not installed hrms's fixtures yet has the field and not the template;
        # naming a template that does not exist would trade the popup for a LinkValidationError.
        if not frappe.db.exists("Email Template", template):
            continue
        settings.set(field, template)
        changed.append(field)

    if changed:
        settings.flags.ignore_permissions = True
        settings.save()

    return changed


def notify_approver_of_new_application(doc, method=None):
    """Tell the leave approver that an application is waiting on them.

    Hooked on Leave Application.after_insert.

    Only for an application that is actually waiting -- `Open`. HR raising leave on an
    employee's behalf and approving it in the same save has nobody to ask, and telling the
    approver to go and decide something already decided is the kind of notification that
    teaches people to ignore notifications.
    """
    if not _notifications_enabled() or doc.status != "Open":
        return

    recipients = _active_users([doc.leave_approver])
    # The approver applying for their own leave, or entering it for somebody else, does not
    # need telling. `make_notification_logs` skips self-notification for every type EXCEPT
    # "Alert", which is the type used here -- so it has to be done explicitly.
    recipients.discard(frappe.session.user)
    if not recipients:
        return

    _notify(
        recipients,
        doc,
        subject=_("{0} has applied for {1}").format(doc.employee_name, _(doc.leave_type)),
        content=_("{0} requested <b>{1}</b> of {2} from {3} to {4}, and it is waiting on you.").format(
            frappe.bold(doc.employee_name),
            doc.total_leave_days,
            _(doc.leave_type),
            frappe.format(doc.from_date, "Date"),
            frappe.format(doc.to_date, "Date"),
        ),
    )


def notify_applicant_of_decision(doc, method=None):
    """Tell the applicant their leave was approved or rejected.

    Hooked on Leave Application.on_update, which frappe runs on a plain save AND ahead of
    `on_submit` -- so this fires once whether the approver decides in a draft and submits
    later, or does both in one action. `has_value_changed` is what keeps it to once: on the
    submit that follows a decision already saved, the status has not moved.

    On insert `has_value_changed` answers True for every field, there being no previous
    version to compare against; the status check is what stops a leave entered directly as
    Approved from being announced twice, once here and once from after_insert.
    """
    if not _notifications_enabled():
        return
    if doc.status not in DECIDED or not doc.has_value_changed("status"):
        return

    _notify_applicant(
        doc,
        subject=_("Your leave application was {0}").format(_(doc.status).lower()),
        content=_("Your <b>{0}</b> from {1} to {2} was {3} by {4}.").format(
            _(doc.leave_type),
            frappe.format(doc.from_date, "Date"),
            frappe.format(doc.to_date, "Date"),
            frappe.bold(_(doc.status).lower()),
            frappe.bold(_user_name(frappe.session.user)),
        ),
    )


def notify_applicant_of_cancellation(doc, method=None):
    """Tell the applicant that approved leave has been cancelled.

    Hooked on Leave Application.on_cancel. A cancellation gives the days back to the balance
    and undoes the attendance, so it changes the applicant's plans as much as the original
    decision did -- and it is the one step of the three that the applicant has no reason to
    be looking out for.
    """
    if not _notifications_enabled():
        return

    _notify_applicant(
        doc,
        subject=_("Your approved leave was cancelled"),
        content=_("Your <b>{0}</b> from {1} to {2} was cancelled by {3}. The days are back on your balance.").format(
            _(doc.leave_type),
            frappe.format(doc.from_date, "Date"),
            frappe.format(doc.to_date, "Date"),
            frappe.bold(_user_name(frappe.session.user)),
        ),
    )


def _notify_applicant(doc, subject, content):
    """Both applicant-facing steps address the same person and drop out the same way."""
    applicant = frappe.db.get_value("Employee", doc.employee, "user_id", cache=True)
    recipients = _active_users([applicant])
    # An approver deciding their own application already knows the answer. An employee with no
    # user_id at all -- common for staff who never log in -- leaves nobody to tell, and the
    # email hrms sends to `company_email`/`personal_email` remains the only channel there.
    recipients.discard(frappe.session.user)
    if not recipients:
        return

    _notify(recipients, doc, subject=subject, content=content)


def _notify(recipients, doc, subject, content):
    enqueue_create_notification(
        sorted(recipients),
        {
            "type": "Alert",
            "subject": subject,
            "email_content": content,
            "document_type": doc.doctype,
            "document_name": doc.name,
            "from_user": frappe.session.user,
            "link": get_url_to_form(doc.doctype, doc.name),
        },
    )


def _notifications_enabled() -> bool:
    """One switch over both channels -- see the module docstring."""
    return bool(frappe.db.get_single_value("HR Settings", "send_leave_notification"))


def _user_name(user: str) -> str:
    return frappe.db.get_value("User", user, "full_name", cache=True) or user
