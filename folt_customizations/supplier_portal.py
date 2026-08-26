"""The supplier's side of FoLT's procurement chain: getting into the portal, and quoting in it.

A FoLT supplier never sees the Desk. Everything they do -- read a Request for Quotation, price
it, revise the price before the deadline -- happens on the website portal under their own login.
Three separate things have to be true for that, and none of them is true on a stock install.

1. LOGGING IN HAS TO LAND ON THE PORTAL.

   It landed in the Desk. `LoginManager.set_user_info` sends a Website User to
   `get_default_path() or "/" + get_home_page()` (frappe/auth.py), and `get_default_path`
   (frappe/apps.py) answers with the route of the only entry on the apps screen -- FoLT's own
   `/desk/folt`, declared in hooks.add_to_apps_screen -- for *every* user, desk access or not.
   A supplier was therefore redirected straight into a Desk workspace they cannot open, and no
   amount of home-page configuration reached them, because `get_default_path()` is consulted
   first and never returned None.

   `desk_app_visible` closes that off through the seam frappe already provides for it,
   `add_to_apps_screen[].has_permission`. With the FoLT tile hidden from portal users the apps
   screen is empty for them, `get_default_path()` returns None as it should, and the login falls
   through to `get_home_page()` -- where `portal_home_page` puts them on the RFQ list.

   Both are hooks rather than stored settings on purpose. The obvious no-code route is
   `Role["Supplier"].home_page`, which `get_home_page` reads before anything else -- and which
   applies to *every* holder of the role, System Users included. FoLT has staff logins carrying
   the Supplier role (they administer the supplier register), and that setting would have
   redirected them out of the Desk and onto the portal at every login.

2. THE PORTAL HAS TO RECOGNISE THEM AS THE SUPPLIER.

   erpnext answers "which supplier is this visitor" from the `portal_users` table on Supplier
   and from nothing else (erpnext/controllers/website_list_for_contact.py:get_parents_for_user).
   A Contact linked to the Supplier, the address the RFQ was emailed to, the Supplier role on
   the user -- none of them counts. A supplier login missing from that table gets an empty RFQ
   list and a bare `Not Permitted` on every RFQ it opens, which is what "the portal doesn't
   work" looks like from the outside.

   erpnext fills the table itself, but only down one path: `update_user_in_supplier`, reached
   when an RFQ is *emailed* to a supplier row that carries an `email_id`. A supplier whose login
   was created by hand in the Desk, or whose RFQ row carried a different address than the login,
   is never added. `link_portal_users` closes the gap from the direction FoLT actually works in
   -- the Contact linked to the Supplier -- and `sync_portal_user` keeps it closed for contacts
   created from then on, so this stops being something to remember.

3. A QUOTATION HAS TO BE REVISABLE.

   erpnext's portal offers "Make Quotation", and that is all it offers: every press calls
   `create_supplier_quotation` and inserts *another* draft. The rates already quoted are not
   loaded back into the page either, so a supplier correcting one line of a ten-line bid retypes
   the other nine and FoLT ends up with two bids from one bidder against one RFQ -- both visible
   to the committee (procurement.rfq_quotations reads drafts), neither marked as superseded.
   `save_quotation` makes the page revisable instead: it writes into the supplier's existing
   draft for that RFQ when there is one, and only inserts when there is not. The page itself --
   which loads those rates back in and says "Update" rather than "Make" -- is in
   templates/pages/rfq.*, which explains why it shadows erpnext's copy.

   Editing stops at submission, deliberately. A submitted quotation is the supplier's formal
   offer and the committee may already be scoring it, so the portal shows it and offers no
   button. Reopening a bid is a buyer's decision (cancel and amend in the Desk), not a
   supplier's.
"""

from contextlib import contextmanager

import frappe
from frappe import _
from frappe.utils import flt, nowdate

from erpnext.buying.doctype.request_for_quotation.request_for_quotation import create_rfq_items

SUPPLIER_ROLE = "Supplier"

# Where a supplier login lands: the list of RFQs they have been invited to quote, which is the
# only page on the portal that starts a piece of work. Everything else a supplier can reach --
# their quotations, purchase orders, invoices -- is one click away in the portal sidebar, so
# this doubles as the portal's front door. `/portal`, which frappe would otherwise pick for a
# portal user, renders the literal words "Welcome to the Portal" and nothing else.
PORTAL_HOME = "rfq"


# --- 1. getting in -------------------------------------------------------------------


def portal_home_page(user: str) -> str | None:
	"""The route a supplier login lands on. Hooked as `get_website_user_home_page`.

	Returning None hands the decision back to frappe, which is what has to happen for every
	other user: the hook is called for whoever is logging in, staff included, and only the
	holders of a portal-only Supplier login are answered here. `user_type` rather than the role
	alone is the test that matters -- see the note on Role.home_page above.
	"""
	if frappe.get_cached_value("User", user, "user_type") != "Website User":
		return None
	if SUPPLIER_ROLE not in frappe.get_roles(user):
		return None
	return PORTAL_HOME


def desk_app_visible() -> bool:
	"""Whether the FoLT tile is offered on the apps screen. `add_to_apps_screen.has_permission`.

	Called with no arguments, for the session user (frappe/apps.py:get_apps). The tile is a Desk
	route, so offering it to a Website User is wrong twice over: it puts a link they cannot
	follow on their apps screen, and -- because the apps screen doubles as frappe's
	"where do I send you after login" answer -- it was redirecting them there.
	"""
	if frappe.session.user in ("Guest", None):
		return False
	return frappe.get_cached_value("User", frappe.session.user, "user_type") == "System User"


# --- 2. being recognised as the supplier ---------------------------------------------


def link_portal_users():
	"""Give every supplier contact that has a login portal access to its own supplier.

	Runs on install and on every migrate. Idempotent: a contact already in the supplier's
	`portal_users` table costs one exists() and nothing else.
	"""
	links = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Contact", "link_doctype": "Supplier"},
		fields=["parent as contact", "link_name as supplier"],
	)
	for link in links:
		user = frappe.db.get_value("Contact", link.contact, "user")
		if user and frappe.db.exists("Supplier", link.supplier):
			grant_portal_access(link.supplier, user)


def sync_portal_user(doc, method=None):
	"""Hooked on Contact.on_update -- covers insert too, so a supplier contact given a login in
	the Desk can use the portal immediately rather than after the next migrate."""
	if not doc.user:
		return
	for link in doc.links:
		if link.link_doctype == "Supplier":
			grant_portal_access(link.link_name, doc.user)


def grant_portal_access(supplier: str, user: str) -> bool:
	"""Add `user` to `supplier`'s portal users, with the Supplier role. True if anything changed.

	The role is granted only to a Website User, and that restriction is the whole safety of
	this function: FoLT staff appear as Contacts against suppliers too (a buyer is often the
	named contact on a record), and their Desk account must not collect roles because somebody
	linked a contact. A Website User has no Desk access at all, so the role only ever decides
	which portal pages they are offered -- and erpnext's own supplier-login path
	(RequestforQuotation.create_user) grants exactly the same thing.
	"""
	# db.get_value rather than get_cached_value: this runs over every supplier contact on every
	# migrate, and a contact pointing at a user that is no longer there must skip the row, not
	# raise DoesNotExistError and take the migrate down with it.
	if frappe.db.get_value("User", user, "user_type") != "Website User":
		return False

	changed = False
	if SUPPLIER_ROLE not in frappe.get_roles(user):
		user_doc = frappe.get_doc("User", user)
		user_doc.append_roles(SUPPLIER_ROLE)
		user_doc.save(ignore_permissions=True)
		changed = True

	if not frappe.db.exists("Portal User", {"parenttype": "Supplier", "parent": supplier, "user": user}):
		# Through the parent document rather than by inserting the child row directly: Supplier
		# has its own validate() (see supplier.py) and erpnext reads this table through the
		# parent's meta, so a row with no idx and no parentfield is a row that half-works.
		supplier_doc = frappe.get_doc("Supplier", supplier)
		supplier_doc.append("portal_users", {"user": user})
		supplier_doc.save(ignore_permissions=True)
		changed = True

	return changed


# --- 3. quoting ----------------------------------------------------------------------


def draft_quotation(request_for_quotation: str, supplier: str) -> str | None:
	"""The supplier's own still-editable quotation against one RFQ, or None.

	The RFQ link lives on Supplier Quotation *Item*, not on the quotation (the same reason
	procurement.rfq_quotations reads through the child table). Newest first, because a site that
	has been quoting through erpnext's stock page can already hold more than one draft per
	bidder -- the newest is the one the supplier last worked on, and the older ones are left
	alone for a buyer to clear up rather than silently rewritten from here.
	"""
	names = frappe.get_all(
		"Supplier Quotation Item",
		filters={"request_for_quotation": request_for_quotation, "docstatus": 0},
		pluck="parent",
		distinct=True,
	)
	if not names:
		return None

	drafts = frappe.get_all(
		"Supplier Quotation",
		filters={"name": ["in", names], "supplier": supplier, "docstatus": 0},
		pluck="name",
		order_by="creation desc",
		limit=1,
	)
	return drafts[0] if drafts else None


@frappe.whitelist()
def save_quotation(request_for_quotation: str, items, terms: str | None = None) -> str:
	"""Write the prices a supplier entered on the portal into their quotation for one RFQ.

	Creates the quotation on the first save and updates it on every save after that, which is
	what makes the portal page revisable. Returns the quotation's name for the page to link to.

	`items` carries qty and rate keyed by RFQ item, and nothing else: every other field on the
	line -- item, description, UOM, warehouse, the material request it came from -- is read
	server-side off the RFQ. What a supplier is being asked to price is FoLT's statement, not
	something a posted form gets to redefine, and rebuilding the table from the RFQ each time
	also means a line added to the RFQ before it was sent cannot go unpriced.
	"""
	rfq = frappe.get_doc("Request for Quotation", request_for_quotation)
	supplier = session_supplier(rfq)
	quoting_user = frappe.session.user

	quoted = {}
	for row in frappe.parse_json(items) or []:
		quoted[row.get("request_for_quotation_item")] = row

	# ERPNext re-derives every item line on every save (AccountsController.validate ->
	# set_missing_values -> get_item_details, which loads the Item and permission-checks it), so
	# writing a quotation needs read on Item -- which a supplier must not have. Item carries
	# `last_purchase_rate` and `valuation_rate` at permlevel 0, so granting the Supplier role
	# read on it would hand every bidder the price FoLT last paid, in a system whose whole point
	# is that they bid without knowing it. So the derivation runs as the system instead, and the
	# quotation is stamped back to the supplier below, because who entered a bid is part of it.
	#
	# This is also why erpnext's own portal button does nothing on a stock install: it makes the
	# same call and swallows the resulting PermissionError (create_supplier_quotation returns
	# None inside a bare `except`), so the page reports no error and no quotation appears.
	with _as_system_user():
		quotation = _write_quotation(rfq, supplier, quoted, terms)

	frappe.db.set_value(
		"Supplier Quotation",
		quotation.name,
		{"owner": quoting_user, "modified_by": quoting_user},
		update_modified=False,
	)
	return quotation.name


def _write_quotation(rfq, supplier: str, quoted: dict, terms: str | None):
	"""Create or update the supplier's draft quotation. Runs as the system -- see save_quotation."""
	name = draft_quotation(rfq.name, supplier)
	if name:
		quotation = frappe.get_doc("Supplier Quotation", name)
	else:
		quotation = frappe.new_doc("Supplier Quotation")
		quotation.supplier = supplier
		quotation.company = rfq.company
		quotation.transaction_date = nowdate()
		quotation.currency = frappe.db.get_value("Supplier", supplier, "default_currency") or frappe.get_cached_value(
			"Company", rfq.company, "default_currency"
		)
		quotation.buying_price_list = frappe.db.get_value(
			"Supplier", supplier, "default_price_list"
		) or frappe.db.get_single_value("Buying Settings", "buying_price_list")

	quotation.terms = terms
	quotation.items = []
	for item in rfq.items:
		line = item.as_dict()
		row = quoted.get(item.name) or {}
		# A blank qty means the supplier did not touch the line, so FoLT's own quantity stands;
		# a blank rate means exactly what it says and is left at zero, so a partial bid is
		# visibly partial rather than quietly complete.
		line.qty = flt(row.get("qty")) or item.qty
		line.rate = flt(row.get("rate"))
		create_rfq_items(quotation, supplier, line)

	quotation.save()
	return quotation


@contextmanager
def _as_system_user():
	"""Run a block as Administrator and hand the request back exactly as it was.

	`frappe.set_user` is the framework's way in but not its way out: it overwrites `session.sid`
	with the username it is given and empties `session.data`, so calling it a second time to
	restore the visitor would leave the request holding a session id that is an email address
	and a `session.data` with no `user_type` in it -- which is what `is_portal_user` and
	`get_home_page` read. The fields it clobbers are therefore snapshotted and put back, and the
	per-user caches it builds are dropped so nothing decided as Administrator outlives the block.
	"""
	session = frappe.local.session
	saved = (session.user, session.sid, session.data)
	frappe.set_user("Administrator")
	try:
		yield
	finally:
		session.user, session.sid, session.data = saved
		frappe.local.cache = {}
		frappe.local.role_permissions = {}
		frappe.local.user_perms = None


def session_supplier(rfq) -> str:
	"""The supplier the session user is quoting for on this RFQ, or PermissionError.

	Two gates, both needed. The user has to be a portal user of the supplier -- the check
	erpnext's own create_supplier_quotation makes, and the one thing that establishes who a
	portal visitor is. And that supplier has to be one this RFQ was actually sent to, or any
	supplier login could price a competition it was never invited to; erpnext leaves that second
	one to the page it happens to be posted from.
	"""
	mine = set(
		frappe.get_all(
			"Portal User",
			filters={"parenttype": "Supplier", "user": frappe.session.user},
			pluck="parent",
		)
	)
	for row in rfq.suppliers:
		if row.supplier in mine:
			return row.supplier

	frappe.throw(_("Not Permitted"), frappe.PermissionError)
