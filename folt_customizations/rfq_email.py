"""The email a supplier gets when FoLT sends them a Request for Quotation.

ERPNext sends that email as the `Message for Supplier` field and nothing else. Shipped default
and all, what lands in a supplier's inbox is one unstyled sentence -- "Please supply the
specified items at the best possible rates" -- with no FoLT mark on it, no statement of what is
being asked for, and, crucially, **no link to the portal where the quotation is actually
entered**. A supplier who has never dealt with FoLT before receives an anonymous line of text
and has no way to act on it.

Everything needed to fix that is already in the controller and simply never reaches the page:
`get_link()` builds the portal URL, `update_supplier_contact()` mints a set-password link for a
supplier who has no login yet, and the document itself carries the items, the required date and
the terms. This module wraps the buyer's message in a FoLT frame that puts all of it in front
of the reader.

WHY AN OVERRIDE AND NOT AN EMAIL TEMPLATE. The obvious no-code route is to type HTML into
`Message for Supplier`, which is rendered with `frappe.render_template` against the document.
Two things rule it out. The portal link and the set-password link are *not* in that context as
URLs -- `supplier_rfq_mail` puts them in pre-wrapped as `<a class="btn btn-default btn-xs">`
anchors, which Outlook renders as bare underlined text, and the set-password one is per
supplier and per send. And a template stored in a field is a template every buyer can silently
break: the frame would live in a Text Editor, one document at a time, rather than in one place
under review. Here the buyer still writes the message -- they just no longer have to build the
email around it.

WHY THE FRAME IS HAND-ROLLED. frappe has a perfectly good framed layout (`with_container` /
`header` in `email_body.get_formatted_html`) and notifications.py uses it for the committee
email. It is unreachable from here: RFQ mail goes out through
`frappe.core.doctype.communication.email.make`, and Communication's `sendmail_input_dict`
(core/doctype/communication/mixins.py) simply does not pass either argument -- every
Communication-borne email is rendered full-width and unbranded, no setting involved. Sending
with `frappe.sendmail` directly would win the frame and lose the Communication record, which is
what puts the sent mail in the RFQ's timeline and threads the supplier's reply back onto it.
So the frame is drawn in the content instead, using frappe's own container measurements (600px,
12px radius, 1px #ededed, 36px gutters, 28px logo -- see public/scss/email.bundle.scss) so the
two emails read as one family.

Relative URLs are safe here: `get_formatted_html` runs `scrub_urls` over the finished email and
expands `/assets/...` against the site URL, the same way it does for frappe's own masthead.
"""

import frappe
from frappe import _
from frappe.utils import escape_html, flt, formatdate, strip_html
from frappe.utils.user import get_user_fullname

# Note the lowercase `f` -- erpnext's class is `RequestforQuotation`, not `RequestForQuotation`.
from erpnext.buying.doctype.request_for_quotation.request_for_quotation import RequestforQuotation

from folt_customizations.branding import EMAIL_ACCENT, LOGO_EMAIL

# How many item rows are printed before the table gives up and points at the portal. An RFQ
# with fifty lines is a document, not an email; the email's job is to let the reader recognise
# the request and click through.
MAX_ITEM_ROWS = 12

# Description is shown under the item name only when it says something the name does not. It is
# a Text Editor field holding specifications, so it is stripped to text and cut -- the full text
# is on the portal page and on the attached print, both a click away.
MAX_DESCRIPTION_CHARS = 160

TEXT = "#171717"
MUTED = "#6b7280"
RULE = "#ededed"


class FoLTRequestForQuotation(RequestforQuotation):
    """Wraps the outgoing supplier email in the FoLT frame. Registered in hooks.override_doctype_class.

    Two seams, because the message is built in one place and sent in another. `supplier_rfq_mail`
    is where the per-supplier links exist, so it stashes them; `send_email` is the last point
    before the Communication is created, so it is where the frame goes on. The preview path
    (`get_supplier_email_preview`, the Preview button on the form) returns from
    `supplier_rfq_mail` without ever reaching `send_email`, so it is framed there instead --
    otherwise a buyer would preview one email and their supplier would receive a different one.
    """

    def supplier_rfq_mail(self, data, update_password_link, rfq_link, preview=False):
        # `flags` rather than a plain attribute: flags is frappe's scratch space on a document,
        # excluded from `as_dict()` -- and `as_dict()` is exactly what becomes the render context
        # for the buyer's message a few lines further down in the parent method.
        self.flags.folt_rfq_links = {
            "portal": rfq_link,
            "set_password": update_password_link,
        }
        result = super().supplier_rfq_mail(data, update_password_link, rfq_link, preview)

        if preview and isinstance(result, dict):
            result["message"] = self.folt_email_html(data, result["message"])

        return result

    def send_email(self, data, sender, subject, message, attachments):
        super().send_email(data, sender, subject, self.folt_email_html(data, message), attachments)

    def folt_email_html(self, supplier_row, message):
        """The buyer's `message`, framed, for one row of the RFQ's supplier table."""
        links = self.flags.get("folt_rfq_links") or {}
        return _render(self, supplier_row, message, links.get("portal"), links.get("set_password"))


def _render(doc, supplier_row, message, portal_link, set_password_link):
    supplier_name = supplier_row.get("supplier_name") or supplier_row.get("supplier") or ""

    # The subject line is the buyer's own description of the request and is worth repeating under
    # the reference -- unless they left the field at its default, in which case it would only
    # restate the masthead.
    subject = doc.subject if doc.subject and doc.subject != _("Request for Quotation") else ""

    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td align="center" style="padding:0">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
       style="width:100%;max-width:600px;border:1px solid {RULE};border-radius:12px;
              background-color:#ffffff;border-spacing:0">

    <tr><td style="padding:24px 36px 20px;border-bottom:1px solid #f0f0f0">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
            <td align="left" valign="middle">
                <img src="{LOGO_EMAIL}" height="28" alt="Friends of Lake Turkana"
                     style="display:block;border:0;outline:none;height:28px;width:auto;max-height:28px">
            </td>
            <td align="right" valign="middle"
                style="font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:{MUTED}">
                {_("Request for Quotation")}
            </td>
        </tr></table>
    </td></tr>

    <tr><td style="padding:28px 36px 0">
        <div style="font-size:20px;font-weight:700;letter-spacing:-.02em;line-height:1.25;color:{TEXT}">
            {escape_html(doc.name)}
        </div>
        {f'<div style="margin-top:4px;font-size:14px;color:{MUTED}">{escape_html(subject)}</div>' if subject else ""}
    </td></tr>

    <tr><td style="padding:20px 36px 28px;font-size:14px;line-height:1.6;color:#525252">
        <p style="margin:0 0 12px">{_("Dear {0},").format(escape_html(supplier_name))}</p>
        <div>{message}</div>

        {_details(doc)}
        {_items(doc)}
        {_terms(doc)}
        {_call_to_action(portal_link, set_password_link, supplier_row)}
    </td></tr>

    <tr><td style="padding:18px 36px 22px;background-color:#fafafa;border-top:1px solid #f3f3f3;
                   border-radius:0 0 12px 12px;font-size:12px;line-height:1.6;color:#999999">
        {_footer(doc, supplier_row)}
    </td></tr>

</table>
</td></tr></table>
"""


def _details(doc):
    """Reference, dates and delivery terms, as a label/value list."""
    rows = [(_("Reference"), doc.name), (_("Issued"), formatdate(doc.transaction_date))]
    if doc.schedule_date:
        rows.append((_("Required by"), formatdate(doc.schedule_date)))
    if doc.incoterm:
        rows.append((_("Incoterm"), " ".join(filter(None, [doc.incoterm, doc.named_place]))))
    rows.append((_("Requested by"), doc.company))

    cells = "".join(
        f'<tr><td style="padding:5px 16px 5px 0;color:{MUTED};white-space:nowrap">{label}</td>'
        f'<td style="padding:5px 0;color:{TEXT}"><b>{escape_html(str(value))}</b></td></tr>'
        for label, value in rows
    )
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0"'
        f' style="margin:20px 0 4px;font-size:13px">{cells}</table>'
    )


def _items(doc):
    """What is being asked for, capped at MAX_ITEM_ROWS."""
    if not doc.items:
        return ""

    shown = doc.items[:MAX_ITEM_ROWS]
    body = "".join(_item_row(item) for item in shown)

    hidden = len(doc.items) - len(shown)
    if hidden:
        body += (
            f'<tr><td colspan="3" style="padding:8px 0;border-top:1px solid #f3f3f3;'
            f'font-size:12px;color:{MUTED}">'
            f'{_("and {0} further item(s) -- the full list is on the portal").format(hidden)}</td></tr>'
        )

    head = (
        f'<tr>'
        f'<th align="left" style="padding:0 8px 6px 0;border-bottom:2px solid #f3f3f3;font-size:11px;'
        f'font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:{MUTED}">{_("Item")}</th>'
        f'<th align="right" style="padding:0 0 6px 8px;border-bottom:2px solid #f3f3f3;font-size:11px;'
        f'font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:{MUTED};white-space:nowrap">'
        f'{_("Quantity")}</th>'
        f'<th align="right" style="padding:0 0 6px 8px;border-bottom:2px solid #f3f3f3;font-size:11px;'
        f'font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:{MUTED};white-space:nowrap">'
        f'{_("Required by")}</th>'
        f'</tr>'
    )
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"'
        f' style="width:100%;margin:20px 0 0;border-collapse:collapse;font-size:13px">'
        f"{head}{body}</table>"
    )


def _item_row(item):
    name = item.item_name or item.item_code or ""
    detail = ""
    if item.item_code and item.item_code != name:
        detail = f'<div style="font-size:11px;color:{MUTED}">{escape_html(item.item_code)}</div>'

    # Description repeats the item name on most rows; it earns its space only when it does not.
    description = strip_html(item.description or "").strip()
    if description and description != name.strip():
        if len(description) > MAX_DESCRIPTION_CHARS:
            description = description[:MAX_DESCRIPTION_CHARS].rstrip() + "..."
        detail += f'<div style="margin-top:2px;font-size:12px;color:{MUTED}">{escape_html(description)}</div>'

    # `:g` on a None qty would raise, and an email that fails to render is worse than one that
    # prints 0 -- the send happens inside the RFQ's on_submit.
    quantity = f"{flt(item.qty):g} {item.uom or ''}".strip()
    required = formatdate(item.schedule_date) if item.schedule_date else "--"
    cell = f"padding:8px 0;border-top:1px solid #f3f3f3;vertical-align:top;color:{TEXT}"

    return (
        f'<tr><td style="{cell};padding-right:8px"><b>{escape_html(name)}</b>{detail}</td>'
        f'<td align="right" style="{cell};padding-left:8px;white-space:nowrap">{escape_html(quantity)}</td>'
        f'<td align="right" style="{cell};padding-left:8px;white-space:nowrap;color:{MUTED}">{required}</td></tr>'
    )


def _call_to_action(portal_link, set_password_link, supplier_row):
    """The portal button -- the point of the whole email.

    The button is a table cell with a background colour rather than a styled `<a>`, because
    Outlook drops padding and background from an anchor and would render the call to action as
    underlined text. Both anchors carry `class="btn"` for a second, unrelated reason: frappe's
    email stylesheet has `.email-body a:not(.btn){text-decoration:underline!important}`, and
    premailer cannot inline a `:not()` selector -- it re-emits the rule in a `<style>` block
    with `!important`, where it beats the anchor's own `text-decoration:none`. Without the
    class the button renders as underlined text in every client that reads the style block. The plain URL is repeated underneath for the clients that strip the button
    anyway, and because a supplier who wants to revise a quotation next week needs an address
    they can keep.
    """
    if not portal_link:
        return ""

    password_block = ""
    if set_password_link:
        email = supplier_row.get("email_id") or ""
        password_block = f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="margin:4px 0 20px;background-color:#f8f8f8;border-radius:8px">
    <tr><td style="padding:14px 16px;font-size:13px;line-height:1.6;color:#525252">
        <b style="color:{TEXT}">{_("First time quoting for FoLT?")}</b><br>
        {_("An account has been opened for {0}. Set a password once, then use the link above whenever you want to check or change your quotation.").format(f'<b>{escape_html(email)}</b>')}
        <div style="margin-top:10px">
            <a href="{set_password_link}" class="btn"
               style="display:inline-block;padding:8px 16px;background-color:#ffffff;
                      border:1px solid #d4d4d4;border-radius:6px;color:{TEXT};font-size:13px;
                      font-weight:600;text-decoration:none">{_("Set your password")}</a>
        </div>
    </td></tr>
</table>"""

    return f"""
<p style="margin:24px 0 0;font-size:14px;line-height:1.6;color:#525252">
    {_("Please enter your prices on the FoLT supplier portal. You can reopen the same link at any time to review or revise your quotation before the required date.")}
</p>
<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:16px 0 12px">
    <tr><td align="center" bgcolor="{EMAIL_ACCENT}" style="border-radius:6px">
        <a href="{portal_link}" class="btn btn-primary"
           style="display:inline-block;padding:11px 22px;background-color:{EMAIL_ACCENT};
                  border:1px solid {EMAIL_ACCENT};border-radius:6px;color:#ffffff;
                  font-size:14px;font-weight:600;text-decoration:none">{_("Submit or update your quotation")}</a>
    </td></tr>
</table>
{password_block}
<p class="text-muted" style="margin:0;font-size:12px;line-height:1.6;color:{MUTED};word-break:break-word">
    {_("Or open this address directly:")}
    <a href="{portal_link}">{portal_link}</a>
</p>
"""


def _terms(doc):
    """FoLT's terms travel with the request, because a price quoted against unseen terms is not
    a bid FoLT can award."""
    if not doc.terms:
        return ""
    heading = doc.tc_name or _("Terms and Conditions")
    return f"""
<div style="margin-top:28px;padding-top:18px;border-top:1px solid {RULE}">
    <div style="margin-bottom:8px;font-size:11px;font-weight:700;letter-spacing:.06em;
                text-transform:uppercase;color:{EMAIL_ACCENT}">{escape_html(heading)}</div>
    <div style="font-size:12px;line-height:1.6;color:{MUTED}">{doc.terms}</div>
</div>"""


def _footer(doc, supplier_row):
    """Who sent it and where it went -- so a supplier knows who to reply to, and a shared
    mailbox can tell which of its addresses FoLT holds on file.

    Named from `doc.owner` rather than from the session user erpnext puts in the `user_fullname`
    render variable: a re-send (`send_supplier_emails`) or a scheduled job runs as somebody else,
    and the supplier's counterpart is the buyer who raised the request, not whoever last pressed
    the button."""
    sender = get_user_fullname(doc.owner)
    lines = [
        _("Issued by {0} for {1}.").format(f'<b style="color:#525252">{escape_html(sender)}</b>',
                                           escape_html(doc.company)),
        _("Sent to {0}. Reply to this email if any part of the request is unclear.").format(
            escape_html(supplier_row.get("email_id") or "")
        ),
    ]
    return "".join(f'<div style="margin-bottom:4px">{line}</div>' for line in lines)
