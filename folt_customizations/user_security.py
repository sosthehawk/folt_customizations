"""The security alert a user gets when their password is changed.

frappe sends that one as two sentences of bare text -- "Your password has been changed and you
might have been logged out of all systems. Please contact the Administrator for further
assistance." -- with no FoLT mark and, worse for a security notice, no facts. It does not say
which account, when, who did it, or whether the change logged the sessions out. A user who did
not change their own password cannot tell from it whether anything is wrong, and one who did
gets an alarming message about contacting an administrator for no reason.

Unlike the RFQ email (see rfq_email.py), this one goes out through `frappe.sendmail` directly
rather than through a Communication, so frappe's own framed layout IS reachable: passing
`with_container` and `header` gets the 600px card, the red "Security Alert" band and the FoLT
masthead logo for free, because `email_body.get_brand_logo` reads the outgoing Email Account's
`brand_logo` -- which `branding._apply_email_brand_logo` already points at the FoLT PNG. Only
the body is built here.

WHY A DOCTYPE OVERRIDE. The alert is sent inline from `User.set_new_password`, with no hook,
no Email Template and no setting between the message and the wire, so the only way to change it
is to own the method. `set_new_password` is mirrored from frappe 16.31.0 and is four lines long;
the password change itself is still `_update_password`, called exactly as upstream calls it.
Re-check this against upstream on any frappe major upgrade -- it is a security path, and a
divergence here would be a silent one.

NOT FIXED HERE: `impersonate()` sends a second unbranded "Security Alert:" email the same way,
but it is a whitelisted module-level function that also writes an Activity Log, writes a
Notification Log and calls `login_manager.impersonate`. Overriding it would mean FoLT re-owning
an entire security flow to restyle an email, which is a bad trade. It fires only when an
administrator impersonates a user, which is rare and already audited.
"""

import frappe
from frappe import _
from frappe.core.doctype.user.user import User
from frappe.utils import escape_html, format_datetime, get_url, now_datetime
from frappe.utils.password import update_password as _update_password
from frappe.utils.user import get_user_fullname

from folt_customizations.branding import (
    EMAIL_ACCENT,
    EMAIL_MUTED as MUTED,
    EMAIL_RULE as RULE,
    EMAIL_TEXT as TEXT,
)


class FoLTUser(User):
    """Sends the password-change alert as a FoLT email. Registered in hooks.override_doctype_class."""

    def set_new_password(self, new_password=None):
        # Mirrors frappe.core.doctype.user.user.User.set_new_password (16.31.0). The password
        # change is upstream's call, unchanged; only the notification below differs.
        if not new_password or self.flags.in_insert:
            return

        _update_password(user=self.name, pwd=new_password, logout_all_sessions=self.logout_all_sessions)

        # Same gate frappe uses: with no usable outgoing account the send would raise, and a
        # failed email must not roll back a password that has already been changed.
        if not frappe.db.exists("Email Account", {"default_outgoing": 1, "awaiting_password": 0}):
            return

        recipient = frappe.db.get_value("User", self.name, "email")
        if not recipient:
            return

        frappe.sendmail(
            recipients=[recipient],
            subject=_("Security alert: your FoLT password was changed"),
            message=_password_changed_body(self, recipient),
            # `header` and `with_container` are what put the message in frappe's framed layout
            # with the FoLT logo in the masthead -- see email_body.get_formatted_html. Red is
            # the indicator frappe reserves for something the reader may need to act on.
            header=[_("Security Alert"), "red"],
            with_container=True,
        )


def _password_changed_body(user, recipient):
    """The alert body: what happened, to which account, when, and what to do about it.

    A security notice is only useful if the reader can tell their own action apart from someone
    else's, so the facts come first and the reassurance second. frappe's version had it the
    other way round -- an instruction to contact the Administrator, addressed identically to the
    person who had just changed their own password and to the person who had not.
    """
    actor = frappe.session.user
    by_self = actor == user.name

    if by_self:
        opening = _("Your FoLT password was changed. If this was you, nothing further is needed.")
        changed_by = _("You")
    else:
        opening = _(
            "The password on your FoLT account was changed by an administrator. You will need the"
            " new password the next time you sign in."
        )
        changed_by = get_user_fullname(actor) or actor

    rows = [
        (_("Account"), recipient),
        (_("When"), format_datetime(now_datetime())),
        (_("Changed by"), changed_by),
        (
            _("Other sessions"),
            _("Signed out everywhere") if user.logout_all_sessions else _("Left signed in"),
        ),
    ]
    # Present only on a real web request -- a password reset run from a script or a background
    # job has no request to read an address off.
    if request_ip := getattr(frappe.local, "request_ip", None):
        rows.append((_("From address"), request_ip))

    details = "".join(
        f'<tr><td style="padding:6px 16px 6px 0;color:{MUTED};white-space:nowrap">{label}</td>'
        f'<td style="padding:6px 0;color:{TEXT}"><b>{escape_html(str(value))}</b></td></tr>'
        for label, value in rows
    )

    login_url = get_url("/login")

    return f"""
<p>{opening}</p>

<table role="presentation" cellpadding="0" cellspacing="0" border="0"
       style="margin:20px 0 4px;font-size:13px">{details}</table>

<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:20px 0 8px">
    <tr><td align="center" bgcolor="{EMAIL_ACCENT}" style="border-radius:6px">
        <a href="{login_url}" class="btn btn-primary"
           style="display:inline-block;padding:11px 22px;background-color:{EMAIL_ACCENT};
                  border:1px solid {EMAIL_ACCENT};border-radius:6px;color:#ffffff;
                  font-size:14px;font-weight:600;text-decoration:none">{_("Sign in to FoLT ERP")}</a>
    </td></tr>
</table>

<div style="margin:16px 0 4px;padding:14px 16px;background-color:#fff8f0;border:1px solid #fde8c7;
            border-radius:8px;font-size:13px;line-height:1.6;color:#8a5a00">
    <b>{_("If you did not expect this change")}</b><br>
    {_("Someone else may have access to your account. Contact the FoLT system administrator straight away and do not sign in until you have.")}
</div>

<p class="text-muted" style="margin:12px 0 0;font-size:12px;line-height:1.6;color:{MUTED};
          border-top:1px solid {RULE};padding-top:12px">
    {_("This is an automatic security notice. Nobody at FoLT will ever ask you for your password by email.")}
</p>
"""
