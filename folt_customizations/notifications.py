import frappe
from frappe import _
from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
from frappe.utils import get_url_to_form
from frappe.utils.user import get_users_with_role

# FoLT runs eight approval workflows, and until now nobody was told when a document landed in
# their queue. Two things were switched off, and both gates have to be open for frappe to send
# anything: `send_email_alert` on the Workflow, AND `send_email` on the Workflow Document State
# the document has just entered. The fixtures now set both everywhere.
#
# That covers email. This module covers the Desk bell, because the email path is gated on an
# outgoing Email Account the site does not have -- `send_workflow_action_email` swallows the
# resulting OutgoingEmailError and logs it, so approvers would still hear nothing. A
# Notification Log entry needs no mail server, and forwards itself as email by itself once one
# is configured (see NotificationLog.after_insert), so this is not throwaway scaffolding.


def notify_pending_approvers(doc, method=None):
    """Tell everyone who can act on a document that it is waiting for them.

    Hooked on Workflow Action.after_insert. Frappe writes exactly one Workflow Action per
    transition, carrying `permitted_roles` -- the roles allowed to make the *next* move -- so
    this is the single point that knows a task has just landed in somebody's queue. Hanging
    the notification here rather than on each document's `on_change` means every FoLT workflow
    is covered, including any added later, without this file naming a single doctype.
    """
    if doc.status != "Open":
        return

    roles = [row.role for row in (doc.permitted_roles or [])]
    if not roles:
        return

    # Frappe writes a Workflow Action for the initial state too, where the pending action is
    # the author's own -- submitting their own draft. Notifying every holder of the author's
    # role about an unsubmitted document is pure noise: before this guard, one new Purchase
    # Order alerted every Purchase User on the site while it was still in Draft.
    #
    # "The author's own to-do" is the actor still being the owner AND holding a role that can
    # make the next move -- not simply "the first state". Employee Advance Float Approval is
    # created in `Requested`, and the action out of it belongs to the Finance Officer, so a
    # rule keyed on the initial state would suppress the one notification that matters there.
    owner = frappe.db.get_value(doc.reference_doctype, doc.reference_name, "owner")
    actor = frappe.session.user
    if actor == owner and not set(roles).isdisjoint(frappe.get_roles(actor)):
        return

    recipients = {user for role in roles for user in get_users_with_role(role)}
    # The person who just moved the document does not need telling they moved it. Worth doing
    # explicitly: `make_notification_logs` skips self-notification for every type EXCEPT
    # "Alert", which is the type used below.
    recipients.discard(frappe.session.user)
    if not recipients:
        return

    title = frappe.db.get_value(doc.reference_doctype, doc.reference_name, "name")
    enqueue_create_notification(
        sorted(recipients),
        {
            "type": "Alert",
            "subject": _("{0} {1} is awaiting your approval").format(
                _(doc.reference_doctype), doc.reference_name
            ),
            "email_content": _("{0} {1} moved to <b>{2}</b> and is waiting on {3}.").format(
                _(doc.reference_doctype),
                frappe.bold(title or doc.reference_name),
                _(doc.workflow_state or ""),
                ", ".join(_(role) for role in sorted(roles)),
            ),
            "document_type": doc.reference_doctype,
            "document_name": doc.reference_name,
            "from_user": frappe.session.user,
            "link": get_url_to_form(doc.reference_doctype, doc.reference_name),
        },
    )
