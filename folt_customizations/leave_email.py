"""The three leave emails, as FoLT documents rather than as unstyled fragments.

leave_notifications.py owns the Desk bell and switched hrms's own emails back on by pointing
HR Settings at the Email Templates hrms ships. This module is about what those emails then
look like, which is the half that wiring them up could not fix.

WHAT WAS WRONG. hrms sends all three leave emails -- to the approver when an application is
raised, and to the applicant when it is decided and when it is later cancelled -- through
`LeaveApplication.notify`, which calls `frappe.sendmail` with neither `with_container` nor
`header`. Every email frappe sends without those two arguments is rendered full-width with no
masthead at all (see email_body.get_formatted_html and templates/emails/standard.html: the
`brand_logo` row is inside `{% if header or with_container %}`), so nothing FoLT sends about
leave carried the FoLT logo. The body was no better: both stock templates are byte-identical
apart from their subject -- an `<h1>Leave Application Notification</h1>` over a five-row
bootstrap table -- so the applicant whose leave had just been rejected and the approver being
asked to decide received the same anonymous page, and neither subject line ("Leave Approval
Notification", "Leave Status Notification") said whose leave, which leave, or what had
happened to it.

WHY A DOCTYPE OVERRIDE. Same reason as user_security.py: there is no hook between the message
and the wire. `notify_employee` and `notify_leave_approver` are called inline from `on_submit`,
`on_update` and `on_cancel`, and the only seam frappe offers over a doctype's own methods is
`override_doctype_class`. This subclass replaces exactly those three methods; every other part
of a leave application -- the balance arithmetic, the ledger entries, the attendance -- is
still upstream's.

The frame is NOT hand-rolled here. Unlike the RFQ email (rfq_email.py), which goes out through
a Communication and so cannot reach frappe's framed layout at all, these go through
`frappe.sendmail` directly, so passing `with_container` and `header` buys the 600px card, the
coloured indicator beside the title and the FoLT masthead for free -- `get_brand_logo` reads
the outgoing Email Account's `brand_logo`, which `branding._apply_email_brand_logo` already
points at the FoLT PNG. Only the body is built here, in the same label/value + call-to-action
shape as the committee and password-change emails, so the three read as one family.

AN ADMINISTRATOR'S OWN TEMPLATE STILL WINS. If HR Settings names an Email Template that is not
the hrms default, that is a decision somebody made, and `notify_employee` /
`notify_leave_approver` fall through to upstream to render it. It still gets the frame and the
logo, because the framing lives in `notify`, one level below. Blank fields are not a decision
-- they are the state that produced hrms's "Please set default template" popup instead of a
notification -- so those take the FoLT body written here.
"""

import frappe
from frappe import _
from frappe.utils import cint, escape_html, formatdate, get_url_to_form
from frappe.utils.user import get_user_fullname

from hrms.hr.doctype.leave_application.leave_application import LeaveApplication
from hrms.utils import get_employee_email

from folt_customizations.branding import (
    EMAIL_ACCENT,
    EMAIL_MUTED as MUTED,
    EMAIL_RULE as RULE,
    EMAIL_TEXT as TEXT,
)
from folt_customizations.leave_notifications import LEAVE_EMAIL_TEMPLATES

# The masthead band above the body: title, and the indicator colour frappe paints beside it.
# Chosen to match what the reader has to do rather than to decorate -- amber is a request
# waiting on somebody, green and red are the two answers, grey is a fact with nothing to act on.
HEADER_REQUEST = "orange"
HEADER_APPROVED = "green"
HEADER_REJECTED = "red"
HEADER_CANCELLED = "gray"

# The tinted outcome panel that opens an applicant's email: border, background, text. A reader
# who opens the mail on a phone sees the answer before they see any of the detail.
PANEL = {
    "Approved": ("#cfe8d5", "#f2f9f4", "#1a6b38"),
    "Rejected": ("#f5d2d2", "#fdf5f5", "#a12a2a"),
    "Cancelled": ("#e4e4e7", "#fafafa", "#525252"),
}


class FoLTLeaveApplication(LeaveApplication):
    """Sends the three leave emails as FoLT emails. Registered in hooks.override_doctype_class."""

    def notify_leave_approver(self):
        """The approver is being asked to decide something. Everything they need to decide it
        without opening the Desk goes in the body; the button is there for when they do."""
        if not self.leave_approver:
            return
        if not _folt_body_applies("leave_approval_notification_template"):
            return super().notify_leave_approver()

        self.notify(
            {
                "message": _approver_body(self),
                "message_to": self.leave_approver,
                "subject": _("Leave request from {0} — awaiting your approval").format(
                    self.employee_name
                ),
                "header": [_("Leave Request"), HEADER_REQUEST],
            }
        )

    def notify_employee(self):
        """The applicant is being told an answer.

        Upstream calls this from `on_submit` for a decision AND from `on_cancel` for a
        cancellation, with no argument to tell the two apart -- which is half of why the stock
        email is so vague. `before_cancel` sets `status` to `Cancelled` before `on_cancel`
        runs, so the status is enough to say which of the three answers this is.
        """
        recipient = get_employee_email(self.employee)
        if not recipient:
            return
        if not _folt_body_applies("leave_status_notification_template"):
            return super().notify_employee()

        subject, header = {
            "Approved": (
                _("Your {0} has been approved").format(_(self.leave_type)),
                [_("Leave Approved"), HEADER_APPROVED],
            ),
            "Rejected": (
                _("Your {0} request was not approved").format(_(self.leave_type)),
                [_("Leave Not Approved"), HEADER_REJECTED],
            ),
        }.get(
            self.status,
            (
                _("Your approved {0} has been cancelled").format(_(self.leave_type)),
                [_("Leave Cancelled"), HEADER_CANCELLED],
            ),
        )

        self.notify(
            {
                "message": _applicant_body(self),
                "message_to": recipient,
                "subject": subject,
                "notify": "employee",
                "header": header,
            }
        )

    def notify(self, args):
        """Mirrors hrms.hr.doctype.leave_application.LeaveApplication.notify, plus the frame.

        Two differences from upstream, and only two. `with_container` and `header` are passed,
        which is what puts the message in the 600px card with the FoLT masthead -- and because
        that happens here rather than in the two callers above, an administrator's own Email
        Template gets the frame as well. And `header` is read out of `args`, so each of the
        three moments can colour its own band.

        Everything else is upstream's, deliberately: `follow_via_email` is still the per
        application opt-out, the sender is still whoever triggered the mail so the recipient can
        reply to a person, and OutgoingEmailError is still swallowed -- an approval must not
        roll back because the mail server is down.
        """
        args = frappe._dict(args)
        if not cint(self.follow_via_email):
            return

        contact = args.message_to
        if not isinstance(contact, list) and args.notify != "employee":
            contact = frappe.db.get_value("User", contact, "email") or contact

        sender = frappe.db.get_value("User", frappe.session.user, "email")

        try:
            frappe.sendmail(
                recipients=contact,
                sender=sender,
                subject=args.subject,
                message=args.message,
                header=args.get("header") or [_("Leave Application"), HEADER_REQUEST],
                with_container=True,
                reference_doctype=self.doctype,
                reference_name=self.name,
            )
            frappe.msgprint(_("Email sent to {0}").format(contact))
        except frappe.OutgoingEmailError:
            pass


def _folt_body_applies(settings_field: str) -> bool:
    """True unless an administrator has named an Email Template of their own.

    Blank counts as "no choice made" -- see the module docstring. So does the hrms default,
    which is only in HR Settings because leave_notifications.apply_leave_notification_templates
    put it there to stop the nag popup.
    """
    chosen = frappe.db.get_single_value("HR Settings", settings_field)
    return not chosen or chosen == LEAVE_EMAIL_TEMPLATES.get(settings_field)


# --- bodies -----------------------------------------------------------------------------------


def _approver_body(doc):
    link = get_url_to_form(doc.doctype, doc.name)
    approver = doc.leave_approver_name or get_user_fullname(doc.leave_approver) or ""

    return f"""
<p style="margin:0 0 12px">{_("Dear {0},").format(escape_html(approver.split(" ")[0] or approver))}</p>
<p style="margin:0 0 4px;font-size:15px;line-height:1.6;color:{TEXT}">
    {_("{0} has applied for {1} and the request is waiting on your decision.").format(
        f'<b>{escape_html(doc.employee_name or "")}</b>', f'<b>{escape_html(_(doc.leave_type))}</b>')}
</p>

{_details_table(doc, for_approver=True)}
{_reason_block(doc)}
{_call_to_action(link, _("Review this request"))}

<p class="text-muted" style="margin:16px 0 0;padding-top:12px;border-top:1px solid {RULE};
          font-size:12px;line-height:1.6;color:{MUTED}">
    {_("Approving the request books the days against {0}'s balance and marks the attendance. Rejecting it leaves the balance untouched.").format(escape_html(doc.employee_name or ""))}
</p>
"""


def _applicant_body(doc):
    link = get_url_to_form(doc.doctype, doc.name)
    decided_by = get_user_fullname(frappe.session.user) or frappe.session.user

    headline, note = {
        "Approved": (
            _("Your leave has been approved."),
            _("The days have been booked against your balance and your attendance is marked. Enjoy the time off."),
        ),
        "Rejected": (
            _("Your leave request was not approved."),
            _("No days have been taken off your balance. Speak to your approver if you would like to discuss it or apply for different dates."),
        ),
    }.get(
        doc.status,
        (
            _("Your approved leave has been cancelled."),
            _("The days are back on your balance and the attendance has been undone. If you still intend to take this leave, please raise a new application."),
        ),
    )

    return f"""
{_outcome_panel(doc, headline, decided_by)}

<p style="margin:0 0 4px;font-size:14px;line-height:1.6;color:#525252">{note}</p>

{_details_table(doc, for_approver=False)}
{_call_to_action(link, _("Open the application"))}

<p class="text-muted" style="margin:16px 0 0;padding-top:12px;border-top:1px solid {RULE};
          font-size:12px;line-height:1.6;color:{MUTED}">
    {_("This is an automatic notification from FoLT ERP. Reply to this email to reach {0}.").format(escape_html(decided_by))}
</p>
"""


def _outcome_panel(doc, headline, decided_by):
    """The answer, before any of the detail -- see PANEL."""
    border, background, colour = PANEL.get(doc.status, PANEL["Cancelled"])
    verb = {"Approved": _("Approved by"), "Rejected": _("Rejected by")}.get(doc.status, _("Cancelled by"))

    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="margin:0 0 20px;background-color:{background};border:1px solid {border};border-radius:8px">
    <tr><td style="padding:16px 18px">
        <div style="font-size:16px;font-weight:700;line-height:1.35;color:{colour}">{headline}</div>
        <div style="margin-top:4px;font-size:13px;color:{MUTED}">
            {verb} <b style="color:{colour}">{escape_html(decided_by)}</b>
        </div>
    </td></tr>
</table>"""


def _details_table(doc, for_approver):
    """The application itself, as label/value rows -- the same shape every FoLT email uses.

    The approver's copy names the employee and their remaining balance, because those are what
    the decision turns on. The applicant's copy leaves both out: they know who they are, and a
    balance captured before the application was decided is a number that misleads more often
    than it helps once the answer is in.
    """
    rows = []
    if for_approver:
        rows.append((_("Employee"), doc.employee_name))
        if doc.department:
            rows.append((_("Department"), doc.department))

    rows.append((_("Leave type"), _(doc.leave_type)))
    rows.append((_("Dates"), _dates(doc)))
    rows.append((_("Days"), _days(doc.total_leave_days)))

    if for_approver:
        rows.append((_("Applied on"), formatdate(doc.posting_date)))
        # `leave_balance` is labelled "Leave Balance Before Application" on the form, and that is
        # what makes it the useful number here: it is what the employee had to spend when they
        # asked, so the approver can see the request against it.
        if doc.leave_balance is not None:
            rows.append((_("Balance before this request"), _days(doc.leave_balance)))
    else:
        rows.append((_("Reference"), doc.name))

    cells = "".join(
        f'<tr><td style="padding:6px 16px 6px 0;color:{MUTED};white-space:nowrap;vertical-align:top">{label}</td>'
        f'<td style="padding:6px 0;color:{TEXT}"><b>{escape_html(str(value))}</b></td></tr>'
        for label, value in rows
        if value not in (None, "")
    )
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0"'
        f' style="margin:18px 0 4px;font-size:13px">{cells}</table>'
    )


def _dates(doc):
    """One date for a single day, a range for anything longer, and the half-day said out loud.

    A half day is the one case where the dates alone are wrong: `from_date` and `to_date` are
    the same, `total_leave_days` is 0.5, and a reader who only skims the dates books a whole
    day out of a person's diary.
    """
    if doc.from_date == doc.to_date:
        span = formatdate(doc.from_date)
    else:
        span = _("{0} to {1}").format(formatdate(doc.from_date), formatdate(doc.to_date))

    if doc.half_day:
        if doc.half_day_date and doc.from_date != doc.to_date:
            return _("{0} (half day on {1})").format(span, formatdate(doc.half_day_date))
        return _("{0} (half day)").format(span)
    return span


def _days(value):
    """"5 days", "1 day", "0.5 days" -- never "5 day(s)".

    The parenthesised plural is what the stock table used and it is the tell of a template
    nobody wrote for a reader. There are only two forms to pick between here, so pick."""
    number = _number(value)
    return _("1 day") if number == "1" else _("{0} days").format(number)


def _number(value):
    """2.0 days reads as a rounding error; 2 days reads as two days. Halves are kept."""
    value = float(value or 0)
    return str(int(value)) if value == int(value) else f"{value:g}"


def _reason_block(doc):
    """The applicant's own words, when they wrote any.

    `description` is a Small Text, so it arrives as plain text with newlines in it and has to be
    escaped before it is put on an HTML page -- an apostrophe or an ampersand in somebody's
    reason is not markup.
    """
    if not (doc.description or "").strip():
        return ""
    reason = escape_html(doc.description.strip()).replace("\n", "<br>")
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="margin:16px 0 4px;background-color:#f8f8f8;border-radius:8px">
    <tr><td style="padding:14px 16px">
        <div style="margin-bottom:6px;font-size:11px;font-weight:700;letter-spacing:.06em;
                    text-transform:uppercase;color:{EMAIL_ACCENT}">{_("Reason given")}</div>
        <div style="font-size:13px;line-height:1.6;color:#525252">{reason}</div>
    </td></tr>
</table>"""


def _call_to_action(link, label):
    """A bordered table cell with an inline background, not a styled anchor -- Outlook drops
    padding and background on an `<a>` and would render the button as bare underlined text."""
    return f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:22px 0 10px">
    <tr><td align="center" bgcolor="{EMAIL_ACCENT}" style="border-radius:6px">
        <a href="{link}" class="btn btn-primary"
           style="display:inline-block;padding:11px 22px;background-color:{EMAIL_ACCENT};
                  border:1px solid {EMAIL_ACCENT};border-radius:6px;color:#ffffff;
                  font-size:14px;font-weight:600;text-decoration:none">{label}</a>
    </td></tr>
</table>
<p class="text-muted" style="margin:0;font-size:12px;line-height:1.6;color:{MUTED};word-break:break-word">
    {_("Or open it directly:")} <a href="{link}">{link}</a>
</p>"""
