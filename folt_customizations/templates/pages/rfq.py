"""The page a supplier prices an RFQ on -- erpnext's, made revisable.

WHY THIS SHADOWS ERPNEXT'S PAGE. `/rfq/<name>` is routed to the template page named "rfq"
(erpnext/hooks.py:website_route_rules), and frappe resolves that name by walking the installed
apps in reverse order (frappe/website/page_renderers/template_page.py:set_template_path). FoLT
is installed last, so this module and rfq.html take the route from erpnext's copies. Three
things had to change in the page itself and none of them is reachable from a hook: the rates
already quoted have to be loaded back into the inputs, the button has to say Update rather than
Make once a draft exists, and a submitted bid has to render read-only. See
folt_customizations/supplier_portal.py for what was wrong with quoting through the stock page.

The trade-off is the usual one for an override: upstream changes to erpnext's rfq.html no longer
reach this site. Everything about the page that is erpnext's business is still erpnext's --
which supplier the visitor is, the currency and price list to quote in, the item rows -- and is
imported below rather than copied.
"""

import frappe
from frappe.utils import flt

from erpnext.templates.pages.rfq import get_link_quotation, update_supplier_details

from folt_customizations import supplier_portal


def get_context(context):
	context.no_cache = 1
	context.show_sidebar = True

	doc = frappe.get_doc("Request for Quotation", frappe.form_dict.name)
	context.doc = doc
	context.parents = frappe.form_dict.parents
	context["title"] = doc.name

	# Resolves *and* authorises: throws PermissionError unless the visitor is a portal user of a
	# supplier this RFQ was sent to. erpnext's page does the same job in two steps (get_supplier
	# then unauthorized_user) and takes the visitor's first supplier rather than the one this
	# competition is addressed to, which locks out anybody quoting for two FoLT vendors.
	doc.supplier = supplier_portal.session_supplier(doc)
	doc.rfq_links = get_link_quotation(doc.supplier, doc.name)
	update_supplier_details(context)

	context.quotation = supplier_portal.draft_quotation(doc.name, doc.supplier)
	context.submitted = [d for d in (doc.rfq_links or []) if d.status != "Draft"]

	# A bid is revisable while it is still a draft. Once submitted the portal shows it and offers
	# nothing -- and offering "Make Quotation" again would only add a second bid from one bidder
	# to the same competition, which is the duplicate the whole page was reworked to avoid.
	context.editable = doc.docstatus == 1 and (bool(context.quotation) or not context.submitted)

	# Prices are shown from the draft when there is one and from the submitted bid otherwise: a
	# supplier who has already bid opens this page to check what they quoted, and a form of zeros
	# would read as "FoLT never got it". `rfq_links` is newest first (get_link_quotation).
	shown = context.quotation or (context.submitted[0].name if context.submitted else None)
	context.folt_terms = frappe.db.get_value("Supplier Quotation", shown, "terms") if shown else ""
	_load_quoted_prices(doc, shown)
	context.folt_rfq = frappe.as_json(
		{
			"rfq": doc.name,
			"number_format": doc.number_format,
			"items": [
				{"request_for_quotation_item": d.name, "idx": d.idx, "qty": d.folt_qty, "rate": d.folt_rate}
				for d in doc.items
			],
		}
	)


def _load_quoted_prices(doc, quotation):
	"""Put the prices already quoted back on the RFQ rows, as folt_qty / folt_rate.

	Carried on the row rather than in a lookup the template has to consult because the template
	and the JS payload both need them and both read `doc.items`. Keyed on
	`request_for_quotation_item`: matching on item code instead would go wrong the moment an RFQ
	asks for the same item twice (two delivery dates, two locations), which FoLT's transport
	requests routinely do.
	"""
	quoted = {}
	if quotation:
		quoted = {
			row.request_for_quotation_item: row
			for row in frappe.get_all(
				"Supplier Quotation Item",
				filters={"parent": quotation},
				fields=["request_for_quotation_item", "qty", "rate"],
			)
		}

	for item in doc.items:
		row = quoted.get(item.name)
		item.folt_qty = flt(row.qty) if row else flt(item.qty)
		item.folt_rate = flt(row.rate) if row else 0.0
		item.folt_amount = item.folt_qty * item.folt_rate
