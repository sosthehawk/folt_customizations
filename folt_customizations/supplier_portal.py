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

4. THE FOLT APP READS THE SAME THINGS.

   The React client (folt-workflow-ui) gives suppliers the same shell staff use, through its BFF,
   as the supplier's own session. `portal_overview` and `portal_rfq` are its two reads; it saves
   through `save_quotation` above, so there is one write path whichever page a supplier uses.
   Both reads scope to the suppliers the session user is a portal user of and to nothing else --
   the same answer to "who is this visitor" as part 2 -- and read with get_all, because a
   Website User holds no desk read on any of these doctypes. That is why they return only the
   fields a supplier may see: never another bidder, never FoLT's price history.

5. AFTER THE ORDER: INVOICING AND PRINTING.

   A supplier invoices an issued order from here (submit_invoice), which becomes a draft Purchase
   Invoice in the Finance Assistant's queue, and prints their own documents (document_pdf). See
   the sections at the end of the module.
"""

from contextlib import contextmanager

import frappe
from frappe import _
from frappe.utils import cint, flt, nowdate

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


# --- 4. reading, for the FoLT app ---------------------------------------------------------

# How much of each list the overview carries: the newest, which is all a supplier works from.
OVERVIEW_LIMIT = 100
ORDERS_LIMIT = 50


def my_suppliers(user: str | None = None) -> list[str]:
	"""The suppliers `user` (the session user by default) is a portal user of."""
	return frappe.get_all(
		"Portal User",
		filters={"parenttype": "Supplier", "user": user or frappe.session.user},
		pluck="parent",
		order_by="parent asc",
	)


def quoted_prices(quotation: str | None) -> dict:
	"""A quotation's lines keyed by the RFQ line they price (request_for_quotation_item).

	Keyed on the RFQ line, not the item code: an RFQ may ask for the same item twice (two dates,
	two routes), which FoLT's transport requests routinely do.
	"""
	if not quotation:
		return {}
	return {
		row.request_for_quotation_item: row
		for row in frappe.get_all(
			"Supplier Quotation Item",
			filters={"parent": quotation},
			fields=["request_for_quotation_item", "qty", "rate"],
		)
	}


def _standing(rfq: str, supplier: str) -> tuple[str, str | None]:
	"""Where `supplier` stands on one RFQ: ("draft", name), ("submitted", name) or ("to_price", None).

	A draft wins over a submitted bid, the same order as the portal page: it is what the supplier
	last worked on and can still change.
	"""
	draft = draft_quotation(rfq, supplier)
	if draft:
		return "draft", draft
	submitted = frappe.db.sql(
		"""select sq.name from `tabSupplier Quotation` sq
		join `tabSupplier Quotation Item` sqi on sqi.parent = sq.name
		where sqi.request_for_quotation = %s and sq.supplier = %s and sq.docstatus = 1
		order by sq.creation desc limit 1""",
		(rfq, supplier),
	)
	return ("submitted", submitted[0][0]) if submitted else ("to_price", None)


@frappe.whitelist(methods=["GET"])
def portal_overview() -> dict:
	"""Everything a supplier login has with FoLT: the RFQs it was sent, its quotations, its orders.

	Empty lists, not an error, for a login that is nobody's portal user -- the client reads that
	as "this account is not set up as a supplier" and says so.
	"""
	mine = my_suppliers()
	out = {"suppliers": [], "rfqs": [], "quotations": [], "orders": [], "invoices": []}
	if not mine:
		return out

	out["suppliers"] = frappe.get_all(
		"Supplier", filters={"name": ["in", mine]}, fields=["name", "supplier_name"], order_by="supplier_name asc"
	)

	invited = frappe.db.sql(
		"""select rfq.name, rfq.title, rfq.transaction_date, rfq.schedule_date, rfqs.supplier,
			(select count(*) from `tabRequest for Quotation Item` i where i.parent = rfq.name) as line_count
		from `tabRequest for Quotation` rfq
		join `tabRequest for Quotation Supplier` rfqs on rfqs.parent = rfq.name
		where rfq.docstatus = 1 and rfqs.supplier in %(mine)s
		order by rfq.transaction_date desc, rfq.name desc
		limit %(limit)s""",
		{"mine": mine, "limit": OVERVIEW_LIMIT},
		as_dict=True,
	)
	totals = {}
	for row in invited:
		status, quotation = _standing(row.name, row.supplier)
		if quotation and quotation not in totals:
			totals[quotation] = frappe.db.get_value("Supplier Quotation", quotation, ["grand_total", "currency"], as_dict=True)
		money = totals.get(quotation) or {}
		out["rfqs"].append(
			{
				"name": row.name,
				"title": row.title or None,
				"transaction_date": str(row.transaction_date) if row.transaction_date else None,
				"schedule_date": str(row.schedule_date) if row.schedule_date else None,
				"supplier": row.supplier,
				"lines": cint(row.line_count),
				"status": status,
				"quotation": quotation,
				"total": flt(money.get("grand_total")) if quotation else None,
				"currency": money.get("currency"),
			}
		)

	quotations = frappe.get_all(
		"Supplier Quotation",
		filters={"supplier": ["in", mine], "docstatus": ["<", 2]},
		fields=["name", "supplier", "status", "docstatus", "grand_total", "currency", "transaction_date", "valid_till"],
		order_by="creation desc",
		limit=OVERVIEW_LIMIT,
	)
	rfq_of = {}
	if quotations:
		for row in frappe.get_all(
			"Supplier Quotation Item",
			filters={"parent": ["in", [q.name for q in quotations]], "request_for_quotation": ["is", "set"]},
			fields=["parent", "request_for_quotation"],
		):
			rfq_of.setdefault(row.parent, row.request_for_quotation)
	for q in quotations:
		out["quotations"].append(
			{
				"name": q.name,
				"supplier": q.supplier,
				"rfq": rfq_of.get(q.name),
				"status": q.status,
				"docstatus": q.docstatus,
				"grand_total": flt(q.grand_total),
				"currency": q.currency,
				"transaction_date": str(q.transaction_date) if q.transaction_date else None,
				"valid_till": str(q.valid_till) if q.valid_till else None,
			}
		)

	orders = frappe.get_all(
		"Purchase Order",
		filters={"supplier": ["in", mine], "docstatus": 1},
		fields=["name", "supplier", "status", "workflow_state", "docstatus", "grand_total", "currency", "transaction_date", "schedule_date", "per_received", "per_billed", "folt_supplier_acknowledged_on"],
		order_by="transaction_date desc, name desc",
		limit=ORDERS_LIMIT,
	)
	lines = {}
	if orders:
		for row in frappe.get_all(
			"Purchase Order Item",
			filters={"parent": ["in", [o.name for o in orders]]},
			fields=["parent", "item_name", "description", "qty", "uom", "rate", "amount", "schedule_date"],
			order_by="idx asc",
		):
			lines.setdefault(row.parent, []).append(
				{
					"item_name": row.item_name,
					"description": row.description,
					"qty": flt(row.qty),
					"uom": row.uom,
					"rate": flt(row.rate),
					"amount": flt(row.amount),
					"schedule_date": str(row.schedule_date) if row.schedule_date else None,
				}
			)
	for o in orders:
		out["orders"].append(
			{
				"name": o.name,
				"supplier": o.supplier,
				"status": o.status,
				"grand_total": flt(o.grand_total),
				"currency": o.currency,
				"transaction_date": str(o.transaction_date) if o.transaction_date else None,
				"schedule_date": str(o.schedule_date) if o.schedule_date else None,
				"per_received": flt(o.per_received),
				"per_billed": flt(o.per_billed),
				"acknowledged_on": str(o.folt_supplier_acknowledged_on) if o.folt_supplier_acknowledged_on else None,
				"can_invoice": _can_invoice(o),
				"items": lines.get(o.name, []),
			}
		)
	out["invoices"] = my_invoices(mine)
	return out


@frappe.whitelist(methods=["GET"])
def portal_rfq(name: str) -> dict:
	"""One RFQ as the supplier prices it: FoLT's lines, with the prices they have already quoted.

	The same rules as the portal page (templates/pages/rfq.py): session_supplier resolves *and*
	authorises; prices come from the draft when there is one and from the submitted bid
	otherwise; and the bid is editable while it is a draft, or while there is no bid yet.
	"""
	doc = frappe.get_doc("Request for Quotation", name)
	if doc.docstatus != 1:
		frappe.throw(_("Not Permitted"), frappe.PermissionError)
	supplier = session_supplier(doc)
	status, shown = _standing(doc.name, supplier)
	quoted = quoted_prices(shown)

	if shown:
		currency, terms = frappe.db.get_value("Supplier Quotation", shown, ["currency", "terms"])
	else:
		terms = None
		currency = frappe.db.get_value("Supplier", supplier, "default_currency") or frappe.get_cached_value(
			"Company", doc.company, "default_currency"
		)

	items = []
	for item in doc.items:
		row = quoted.get(item.name)
		items.append(
			{
				"request_for_quotation_item": item.name,
				"idx": item.idx,
				"item_code": item.item_code,
				"item_name": item.item_name,
				"description": item.description,
				"qty": flt(item.qty),
				"uom": item.uom,
				"schedule_date": str(item.schedule_date) if item.schedule_date else None,
				"quoted_qty": flt(row.qty) if row else None,
				"rate": flt(row.rate) if row else None,
			}
		)

	return {
		"name": doc.name,
		"title": doc.title or None,
		"company": doc.company,
		"transaction_date": str(doc.transaction_date) if doc.transaction_date else None,
		"schedule_date": str(doc.schedule_date) if doc.schedule_date else None,
		"supplier": supplier,
		"supplier_name": frappe.db.get_value("Supplier", supplier, "supplier_name"),
		"message": doc.message_for_supplier or None,
		"rfq_terms": doc.terms or None,
		"currency": currency,
		"status": status,
		"quotation": shown,
		"editable": status != "submitted",
		"terms": terms or None,
		"items": items,
	}



@frappe.whitelist(methods=["POST"])
def acknowledge_order(name: str) -> dict:
	"""The supplier says they have the order (ADR-008). Only a portal user of the order's supplier,
	only on a submitted order, once: the first acknowledgement is the one kept. Written on the
	submitted order's allow-on-submit fields as the system, the way save_quotation writes bids --
	a portal user holds no write on Purchase Order -- and noted in the order's timeline."""
	order = frappe.db.get_value("Purchase Order", name, ["supplier", "docstatus", "folt_supplier_acknowledged_on"], as_dict=True)
	if not order or order.docstatus != 1 or order.supplier not in my_suppliers():
		frappe.throw(_("That is not one of your orders."), frappe.PermissionError)
	if order.folt_supplier_acknowledged_on:
		return {"name": name, "acknowledged_on": str(order.folt_supplier_acknowledged_on)}
	from frappe.utils import now_datetime

	when = now_datetime()
	user = frappe.session.user
	frappe.db.set_value("Purchase Order", name, {"folt_supplier_acknowledged_on": when, "folt_supplier_acknowledged_by": user}, update_modified=False)
	frappe.get_doc("Purchase Order", name).add_comment("Info", _("Acknowledged by the supplier ({0}) on the supplier portal.").format(user))
	return {"name": name, "acknowledged_on": str(when)}


# --- 5. invoicing, after the order is issued ------------------------------------------------
#
# FoLT's procurement workflow, step 5: once the goods or services are delivered "the supplier
# submits an invoice", and the Finance Assistant reviews it for accuracy and completeness before
# forwarding it for approval (invoice_review.py). The supplier submits it here: against one of
# their issued orders, with their own invoice number and date, the lines and quantities they are
# billing, and the invoice itself attached. What lands in FoLT is a DRAFT Purchase Invoice made by
# ERPNext's own mapper from the order -- prices, taxes and accounts are the order's, never the
# supplier's typing -- waiting in the Finance Assistant's queue. Nothing posts to the ledger until
# FoLT's own approval does it.
#
# The order has to be issued: submitted and at its workflow's Approved state. A line can be billed
# up to what is ordered less what is already invoiced, counting invoices FoLT has not approved yet
# (so a supplier cannot bill the same quantity twice while the first is being checked). Whether
# the delivery has been received is the three-way match's question (match.py), not the portal's:
# an invoice ahead of the receipt is accepted and flagged, as it would be on paper.

INVOICE_FILE_TYPES = {".pdf", ".png", ".jpg", ".jpeg"}
SUPPLIER_INVOICE_FIELD = "folt_submitted_by_supplier"
REVIEWER_ROLE = "Finance Assistant"


def _my_order(name: str):
	order = frappe.db.get_value(
		"Purchase Order", name, ["name", "supplier", "docstatus", "status", "workflow_state", "currency", "company"], as_dict=True
	)
	if not order or order.supplier not in my_suppliers():
		frappe.throw(_("That is not one of your orders."), frappe.PermissionError)
	return order


def _invoice_refusal(order) -> str | None:
	"""Why this order cannot be invoiced from the portal now, or None if it can."""
	if order.docstatus != 1 or (order.workflow_state and order.workflow_state != "Approved"):
		return _("The order has not been issued yet.")
	if order.status in ("Closed", "On Hold"):
		return _("The order is {0}.").format(_(order.status).lower())
	return None


def _invoiced_qty(po_details: list[str]) -> dict:
	"""Quantity already on an invoice per order line, counting drafts and approvals in progress."""
	if not po_details:
		return {}
	rows = frappe.db.sql(
		"""select pii.po_detail, sum(pii.qty) from `tabPurchase Invoice Item` pii
		join `tabPurchase Invoice` pi on pi.name = pii.parent
		where pii.po_detail in %(lines)s and pi.docstatus < 2 and pi.is_return = 0
		group by pii.po_detail""",
		{"lines": po_details},
	)
	return {line: flt(qty) for line, qty in rows}


def _order_lines(order_name: str) -> list[dict]:
	lines = frappe.get_all(
		"Purchase Order Item",
		filters={"parent": order_name},
		fields=["name", "item_name", "description", "qty", "uom", "rate", "received_qty"],
		order_by="idx asc",
	)
	billed = _invoiced_qty([row.name for row in lines])
	return [
		{
			"po_detail": row.name,
			"item_name": row.item_name,
			"description": row.description,
			"uom": row.uom,
			"rate": flt(row.rate),
			"qty": flt(row.qty),
			"received_qty": flt(row.received_qty),
			"invoiced_qty": billed.get(row.name, 0.0),
			"to_invoice": max(flt(row.qty) - billed.get(row.name, 0.0), 0.0),
		}
		for row in lines
	]


def _can_invoice(order) -> bool:
	return not _invoice_refusal(order) and any(line["to_invoice"] > 0 for line in _order_lines(order.name))


@frappe.whitelist(methods=["GET"])
def invoice_form(order: str) -> dict:
	"""What the supplier may invoice on one of their orders: each line, ordered, received, already
	invoiced and still to invoice, at the order's price."""
	po = _my_order(order)
	lines = _order_lines(po.name)
	refusal = _invoice_refusal(po) or (None if any(l["to_invoice"] > 0 for l in lines) else _("Everything on this order has been invoiced."))
	return {
		"order": po.name,
		"supplier": po.supplier,
		"currency": po.currency,
		"can_invoice": refusal is None,
		"reason": refusal,
		"lines": lines,
	}


@frappe.whitelist(methods=["POST"])
def submit_invoice(order: str, bill_no: str, bill_date: str, lines) -> dict:
	"""The supplier sends their invoice for an issued order (multipart: these fields + `file`)."""
	from frappe.utils import getdate, now_datetime

	po = _my_order(order)
	refusal = _invoice_refusal(po)
	if refusal:
		frappe.throw(refusal, title=_("Cannot invoice this order"))

	bill_no = (bill_no or "").strip()
	if not bill_no or len(bill_no) > 140:
		frappe.throw(_("Give your invoice number."), title=_("Invoice number"))
	if not bill_date or getdate(bill_date) > getdate(nowdate()):
		frappe.throw(_("The invoice date cannot be in the future."), title=_("Invoice date"))
	if frappe.db.exists("Purchase Invoice", {"supplier": po.supplier, "bill_no": bill_no, "docstatus": ["<", 2]}):
		frappe.throw(_("You have already sent FoLT an invoice numbered {0}.").format(frappe.bold(bill_no)), title=_("Duplicate invoice number"))

	billable = {line["po_detail"]: line for line in _order_lines(po.name)}
	wanted = {}
	for row in frappe.parse_json(lines) or []:
		line, qty = (row or {}).get("po_detail"), flt((row or {}).get("qty"))
		if line not in billable:
			frappe.throw(_("A line is not on order {0}. Reload it and try again.").format(po.name))
		if qty < 0 or qty > billable[line]["to_invoice"] + 1e-9:
			frappe.throw(
				_("{0}: you can invoice up to {1} {2}.").format(billable[line]["item_name"], billable[line]["to_invoice"], billable[line]["uom"] or ""),
				title=_("Quantity"),
			)
		if qty:
			wanted[line] = qty
	if not wanted:
		frappe.throw(_("Invoice at least one line."), title=_("Nothing invoiced"))

	file_name, content = _uploaded_file()

	user = frappe.session.user
	# As the system, the way bids are written (save_quotation): ERPNext's mapper and the invoice's
	# own validation read Item, Account and the order, none of which a supplier may read.
	with _as_system_user():
		from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_invoice

		invoice = make_purchase_invoice(po.name)
		invoice.set("items", [row for row in invoice.items if row.po_detail in wanted])
		for row in invoice.items:
			row.qty = wanted[row.po_detail]
		invoice.bill_no = bill_no
		invoice.bill_date = bill_date
		invoice.posting_date = nowdate()
		invoice.set_posting_time = 1
		if invoice.meta.has_field(SUPPLIER_INVOICE_FIELD):
			invoice.set(SUPPLIER_INVOICE_FIELD, user)
		invoice.flags.ignore_permissions = True
		invoice.insert()
		frappe.get_doc(
			{
				"doctype": "File",
				"file_name": file_name,
				"content": content,
				"attached_to_doctype": "Purchase Invoice",
				"attached_to_name": invoice.name,
				"is_private": 1,
			}
		).insert(ignore_permissions=True)
		invoice.add_comment("Info", _("Submitted by the supplier ({0}) on the supplier portal against {1}.").format(user, po.name))

	frappe.db.set_value("Purchase Invoice", invoice.name, {"owner": user, "modified_by": user}, update_modified=False)
	_tell_finance(invoice.name, po.supplier, bill_no)
	return {"name": invoice.name, "bill_no": bill_no, "submitted_on": str(now_datetime())}


def _uploaded_file() -> tuple[str, bytes]:
	"""The invoice copy posted with the form (`file`), checked: a PDF or an image, not empty."""
	import os

	request = getattr(frappe, "request", None)
	upload = request.files.get("file") if request is not None and getattr(request, "files", None) else None
	if not upload or not upload.filename:
		frappe.throw(_("Attach your invoice (PDF or a photo of it)."), title=_("Invoice copy"))
	file_name = os.path.basename(upload.filename)
	if os.path.splitext(file_name)[1].lower() not in INVOICE_FILE_TYPES:
		frappe.throw(_("Attach the invoice as a PDF, PNG or JPG."), title=_("Invoice copy"))
	content = upload.stream.read()
	if not content:
		frappe.throw(_("The attached file is empty."), title=_("Invoice copy"))
	return file_name, content


def _tell_finance(invoice: str, supplier: str, bill_no: str):
	from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
	from frappe.utils.user import get_users_with_role

	users = sorted(set(get_users_with_role(REVIEWER_ROLE)))
	if users:
		enqueue_create_notification(
			users,
			{
				"type": "Alert",
				"subject": _("{0} sent invoice {1}: review {2} and forward it for approval").format(supplier, bill_no, invoice),
				"document_type": "Purchase Invoice",
				"document_name": invoice,
				"from_user": frappe.session.user,
			},
		)


# Where a supplier stands on an invoice, in their terms.
def _invoice_standing(row) -> str:
	if row.docstatus == 0:
		return "approval" if row.workflow_state == "Pending Approval" else "received"
	if flt(row.outstanding_amount) <= 0:
		return "paid"
	return "part_paid" if row.status == "Partly Paid" else "approved"


def my_invoices(mine: list[str]) -> list[dict]:
	rows = frappe.get_all(
		"Purchase Invoice",
		filters={"supplier": ["in", mine], "docstatus": ["<", 2], "is_return": 0},
		fields=["name", "supplier", "bill_no", "bill_date", "posting_date", "grand_total", "outstanding_amount", "currency", "status", "docstatus", "workflow_state"],
		order_by="creation desc",
		limit=OVERVIEW_LIMIT,
	)
	orders = {}
	if rows:
		for item in frappe.get_all(
			"Purchase Invoice Item",
			filters={"parent": ["in", [r.name for r in rows]], "purchase_order": ["is", "set"]},
			fields=["parent", "purchase_order"],
		):
			orders.setdefault(item.parent, item.purchase_order)
	return [
		{
			"name": r.name,
			"supplier": r.supplier,
			"bill_no": r.bill_no or None,
			"bill_date": str(r.bill_date) if r.bill_date else None,
			"order": orders.get(r.name),
			"grand_total": flt(r.grand_total),
			"outstanding": flt(r.outstanding_amount) if r.docstatus == 1 else flt(r.grand_total),
			"currency": r.currency,
			"status": _invoice_standing(r),
		}
		for r in rows
	]


# --- 6. printing, the supplier's own documents --------------------------------------------
#
# A supplier prints what FoLT sent them and what they sent FoLT: the RFQ (as addressed to them),
# their quotation, the LPO and their invoices. Rendered as the system in a fixed format per
# doctype, after the same "is this yours" check as every read here -- a Website User holds no
# print right on these doctypes, and choosing the format server-side is what keeps FoLT's own
# fields (match notes, review stamps, other bidders) off the page.

SUPPLIER_PRINTS = {
	"Request for Quotation": "Request for Quotation with Item Image",
	"Supplier Quotation": "Standard",
	"Purchase Order": "FoLT Local Purchase Order",
	"Purchase Invoice": "Purchase Invoice Standard",
}


def _printable(doctype: str, name: str):
	"""The document, readied for the supplier's print, or PermissionError."""
	if doctype not in SUPPLIER_PRINTS:
		frappe.throw(_("Not Permitted"), frappe.PermissionError)
	if not frappe.db.exists(doctype, name):
		frappe.throw(_("Not Permitted"), frappe.PermissionError)
	doc = frappe.get_doc(doctype, name)
	if doctype == "Request for Quotation":
		if doc.docstatus != 1:
			frappe.throw(_("Not Permitted"), frappe.PermissionError)
		doc.update_supplier_part_no(session_supplier(doc))
		return doc
	if doc.supplier not in my_suppliers() or doc.docstatus == 2:
		frappe.throw(_("Not Permitted"), frappe.PermissionError)
	if doctype == "Purchase Order" and doc.docstatus != 1:
		frappe.throw(_("Not Permitted"), frappe.PermissionError)
	return doc


@frappe.whitelist(methods=["GET"])
def document_pdf(doctype: str, name: str):
	"""One of the supplier's own documents as a PDF (the response is the file)."""
	doc = _printable(doctype, name)
	print_format = SUPPLIER_PRINTS[doctype]
	if print_format != "Standard" and not frappe.db.exists("Print Format", print_format):
		print_format = "Standard"
	with _as_system_user():
		pdf = frappe.get_print(doctype, name, print_format, doc=doc, as_pdf=True)
	frappe.local.response.filename = f"{name}.pdf"
	frappe.local.response.filecontent = pdf
	frappe.local.response.type = "pdf"
