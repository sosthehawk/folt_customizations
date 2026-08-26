import frappe
from frappe import _
from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
from frappe.utils import get_url_to_form
from frappe.utils.user import get_users_with_role

# The call to action belongs to the same lockup as the FoLT logo in the masthead above it,
# so the accent is the shared one -- see branding.EMAIL_ACCENT.
from folt_customizations.branding import EMAIL_ACCENT
from folt_customizations.procurement import COMMITTEE_REVIEW_STATE, EVALUATION_DOCTYPE

# FoLT runs nine approval workflows, and until now nobody was told when a document landed in
# their queue. Two things were switched off, and both gates have to be open for frappe to send
# anything: `send_email_alert` on the Workflow, AND `send_email` on the Workflow Document State
# the document has just entered. The fixtures set both everywhere bar one state, `Committee
# Reviewing`, which has a named audience of its own -- see notify_committee_members below.
#
# That covers email. This module covers the Desk bell, because the email path is gated on an
# outgoing Email Account -- `send_workflow_action_email` swallows the resulting
# OutgoingEmailError and logs it, so approvers would hear nothing on a site without one. A
# Notification Log entry needs no mail server at all, so the bell always lands.
#
# The bell does NOT double as email: type "Alert" is in frappe's `notification_skip_email_types`
# hook, so notification_log.after_insert deliberately skips mailing it. Anything that has to
# reach an inbox has to send its own -- see notify_committee_members below.


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

    # A Procurement Committee Evaluation names its own committee, and notify_committee_members
    # has already told exactly those people to go and score. Broadcasting to the whole
    # `Procurement Committee` role on top of that reaches the wrong people, twice over.
    if doc.reference_doctype == EVALUATION_DOCTYPE and doc.workflow_state == COMMITTEE_REVIEW_STATE:
        if frappe.db.exists(
            "Procurement Committee Member",
            {"parent": doc.reference_name, "parenttype": EVALUATION_DOCTYPE, "parentfield": "members"},
        ):
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


# --- Procurement Committee Evaluation --------------------------------------------------------
# The role broadcast above answers "who can move this document"; a competitive bidding
# evaluation also has to answer "who was appointed to score this RFQ", and those are not the
# same people. FoLT's committee for a given RFQ is the `members` table on the document -- the
# `Procurement Committee` role is everyone who has ever sat on one.


def notify_committee_members(doc):
    """Ask the members named on an evaluation to go and score the quotations.

    Called from ProcurementCommitteeEvaluation.on_update when the document enters
    `Committee Reviewing`.

    Sends the bell notification AND an email, because in v16 those are two separate deliveries
    and neither is a substitute for the other. Notification type "Alert" is listed in frappe's
    `notification_skip_email_types` hook, so a Notification Log of that type never mails itself
    (notification_log.after_insert -> is_email_notifications_enabled_for_type). The bell needs no
    mail server and always lands; the email is sent explicitly here and is best-effort.

    The workflow's own role-wide email for this state is switched off in fixtures/workflow.json
    (`send_email: 0` on the `Committee Reviewing` Workflow Document State) so the two do not
    both land. Only the email is gated on that flag -- frappe still writes the Workflow Action
    records, so the pending-approver machinery above is unaffected.
    """
    recipients = _active_users(row.member for row in (doc.members or []) if row.member)
    # Whoever just sent the document to the committee does not need telling that they did. They
    # may well be on the committee themselves -- only `requested_by` is barred from it.
    recipients.discard(frappe.session.user)
    if not recipients:
        return

    quotations = len({row.supplier_quotation for row in (doc.quotation_scores or []) if row.supplier_quotation})
    link = get_url_to_form(doc.doctype, doc.name)
    subject = _("{0} is waiting for your committee score").format(doc.name)

    # The bell has a line of its own: it is read in a dropdown next to the document it points
    # at, so it needs the ask and nothing else. The email carries the same ask with the detail
    # a reader outside the Desk has no other way to get.
    enqueue_create_notification(
        sorted(recipients),
        {
            "type": "Alert",
            "subject": subject,
            "email_content": _(
                "{0} is with the Procurement Committee. Please score the supplier quotations and"
                " tick <b>Reviewed / Signed</b> on your row."
            ).format(frappe.bold(doc.name)),
            "document_type": doc.doctype,
            "document_name": doc.name,
            "from_user": frappe.session.user,
            "link": link,
        },
    )

    try:
        frappe.sendmail(
            recipients=sorted(recipients),
            subject=subject,
            message=_committee_email_body(doc, link, quotations),
            # `with_container` and `header` are what put the email in frappe's framed layout,
            # with the FoLT logo in the masthead (branding._apply_email_brand_logo) and a
            # coloured indicator beside the title. Without either, standard.html renders the
            # message full-width and unbranded -- see email_body.get_formatted_html.
            header=[_("Committee Evaluation"), "orange"],
            with_container=True,
            reference_doctype=doc.doctype,
            reference_name=doc.name,
        )
    except frappe.OutgoingEmailError:
        # A site with no default outgoing account still gets the bell. Losing the email is not
        # worth rolling back the workflow transition that triggered it.
        doc.log_error(_("Could not email the procurement committee"))


def _committee_email_body(doc, link, quotations):
    """The committee email as HTML, styled for a mail client rather than for the Desk.

    Two constraints shape this. Frappe inlines its own email stylesheet over the result
    (`inline_style_in_html` -> premailer), so `table`, `btn btn-primary` and `text-muted` are
    reused from there instead of restated -- the email then matches every other email the site
    sends. And a mail client is not a browser: the button is a bordered table cell with an
    inline background rather than a styled `<a>`, because Outlook drops padding and background
    on an anchor and would render the call to action as bare underlined text.

    The link is whatever `get_url_to_form` resolves to, which is the site's `host_name` -- set
    that per site (`bench set-config host_name http://host:port`) or every link generated
    outside a web request points at a hostname with no port on it.
    """
    rows = [(_("Request for Quotation"), doc.request_for_quotation or _("Not linked"))]
    if doc.activity_requisition:
        rows.append((_("Activity Requisition"), doc.activity_requisition))
    rows.append((_("Quotations to score"), quotations or _("None received yet")))
    rows.append((_("Committee"), _("{0} member(s)").format(len(doc.members or []))))

    detail = "".join(
        f'<tr><td style="padding:6px 12px 6px 0;color:#6b7280;white-space:nowrap">{label}</td>'
        f'<td style="padding:6px 0"><b>{value}</b></td></tr>'
        for label, value in rows
    )

    return f"""
<p>{_("You have been appointed to the procurement committee for this evaluation.")}</p>
<p>{_("Please score each supplier quotation on the evaluation grid, then tick <b>Reviewed / Signed</b> on your own row. The award cannot be recommended until a quorum of members has signed off.")}</p>
<table role="presentation" cellpadding="0" cellspacing="0" border="0"
       style="margin:20px 0;font-size:14px">{detail}</table>
<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:24px 0">
    <tr><td align="center" bgcolor="{EMAIL_ACCENT}" style="border-radius:6px">
        <a href="{link}" class="btn btn-primary"
           style="display:inline-block;padding:11px 22px;background-color:{EMAIL_ACCENT};
                  border:1px solid {EMAIL_ACCENT};border-radius:6px;color:#ffffff;
                  font-size:14px;font-weight:600;text-decoration:none">{_("Score the quotations")}</a>
    </td></tr>
</table>
<p class="text-muted" style="font-size:12px">{_("Or open it directly:")} <a href="{link}">{link}</a></p>
"""


def _active_users(users) -> set[str]:
    """The enabled subset of `users` -- notifying a deactivated login is a dead end."""
    users = {user for user in users if user}
    if not users:
        return set()
    return set(frappe.get_all("User", filters={"name": ["in", list(users)], "enabled": 1}, pluck="name"))
