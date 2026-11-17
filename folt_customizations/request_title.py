"""The request title: the words a purchase is known by, from requisition to invoice.

A purchase starts as a requisition (Material Request) and becomes an RFQ, bids, a committee award
or waiver, an order, a goods receipt and a supplier invoice. Each has its own system code
(MAT-MR-2026-00070, PUR-ORD-2026-00012...) and, in ERPNext, a title that is the supplier's name --
so nobody could tell which purchase an order was without opening it. Every document on the chain
now carries:

* `folt_request_title` -- what is being bought, in words. On the requisition it is what the
  requester typed (or, left empty, the items, as before); ERPNext's own hidden `title` follows it
  so the Desk shows the same words.
* `folt_request` -- on every document after the requisition, the requisition's reference number.
  Read-only: it is set from the lines, never typed.

ERPNext's makers copy a header field to the document they make when both have it, so the two
fields ride along by themselves (RFQ -> bid -> order -> receipt -> invoice). `carry` is the
safety net for everything else -- a document saved through REST or the Desk, a committee
evaluation or waiver (no maker), a line added by hand: on every draft save, an empty field is
filled from the first upstream document that has it. Nothing is ever overwritten, so a title a
buyer changed on purpose stays. Codes, names and links are untouched: this is presentation only.
"""

import frappe

MR = "Material Request"

# doctype -> where it names an upstream document, in the order to trust them:
# (child table field or None for the header, link field, the doctype it names).
UPSTREAM = {
	"Request for Quotation": [("items", "material_request", MR)],
	"Supplier Quotation": [("items", "material_request", MR), ("items", "request_for_quotation", "Request for Quotation")],
	"Procurement Committee Evaluation": [(None, "request_for_quotation", "Request for Quotation")],
	"Derogation Waiver Request": [(None, "supplier_quotation", "Supplier Quotation")],
	"Purchase Order": [
		("items", "material_request", MR),
		("items", "supplier_quotation", "Supplier Quotation"),
		(None, "folt_committee_evaluation", "Procurement Committee Evaluation"),
		(None, "folt_waiver_request", "Derogation Waiver Request"),
	],
	"Purchase Receipt": [("items", "material_request", MR), ("items", "purchase_order", "Purchase Order")],
	"Purchase Invoice": [("items", "purchase_order", "Purchase Order"), ("items", "purchase_receipt", "Purchase Receipt")],
}
CHAIN = (MR, *UPSTREAM)


def _linked(doc, table, field):
	if table is None:
		value = doc.get(field)
		return [value] if value else []
	return list(dict.fromkeys(r.get(field) for r in doc.get(table) or [] if r.get(field)))


def _source(doctype, name):
	"""(reference, title) of one upstream document, read without a permission check: the reader
	already holds the link, and only these two values travel."""
	if doctype == MR:
		row = frappe.db.get_value(MR, name, ["folt_request_title", "title"], as_dict=True)
		return (name, (row.folt_request_title or row.title)) if row else (None, None)
	row = frappe.db.get_value(doctype, name, ["folt_request", "folt_request_title"], as_dict=True)
	return (row.folt_request, row.folt_request_title) if row else (None, None)


def resolve(doc):
	"""The requisition reference and request title this document inherits, or (None, None)."""
	ref = title = None
	for table, field, target in UPSTREAM.get(doc.doctype, ()):
		for name in _linked(doc, table, field):
			r, t = _source(target, name)
			ref, title = ref or r, title or t
			if ref and title:
				return ref, title
	return ref, title


def carry(doc, method=None):
	"""validate on every buying document after the requisition: fill what is empty, never overwrite."""
	if doc.doctype not in UPSTREAM or not doc.meta.has_field("folt_request_title"):
		return
	if doc.get("folt_request") and doc.get("folt_request_title"):
		return
	ref, title = resolve(doc)
	if ref and doc.meta.has_field("folt_request") and not doc.get("folt_request"):
		doc.folt_request = ref
	if title and not doc.get("folt_request_title"):
		doc.folt_request_title = title


def backfill():
	"""Give the documents saved before the fields existed their reference and title, upstream
	first so each step inherits from the one before. Written straight to the row (no save, no
	`modified` change): the documents themselves, submitted or not, are not touched otherwise."""
	from folt_customizations import material_request

	for name in frappe.get_all(MR, filters={"folt_request_title": ["in", ["", None]]}, pluck="name"):
		title = frappe.db.get_value(MR, name, "title")
		if not title or "{" in title:
			doc = frappe.get_doc(MR, name)
			material_request._title(doc)
			title = doc.title
		frappe.db.set_value(MR, name, {"folt_request_title": title, "title": title}, update_modified=False)
	for doctype in UPSTREAM:
		if not frappe.get_meta(doctype).has_field("folt_request_title"):
			continue
		for name in frappe.get_all(doctype, or_filters={"folt_request_title": ["in", ["", None]], "folt_request": ["in", ["", None]]}, pluck="name"):
			doc = frappe.get_doc(doctype, name)
			ref, title = resolve(doc)
			values = {}
			if ref and not doc.get("folt_request"):
				values["folt_request"] = ref
			if title and not doc.get("folt_request_title"):
				values["folt_request_title"] = title
			if values:
				frappe.db.set_value(doctype, name, values, update_modified=False)


def title_fields(meta, preferred=None) -> list[str]:
	"""What a document is called, best first, for the screens that name documents generically
	(Home's queue, Tasks, the activity feed, link titles): a buying document by its request title,
	then by the field that named it before (`preferred`, else the doctype's title_field -- on most
	buying documents the supplier's name), for the ones bought before there were request titles."""
	out = []
	if meta.name in CHAIN and meta.has_field("folt_request_title"):
		out.append("folt_request_title")
	fallback = preferred or meta.title_field
	if fallback and fallback not in out:
		out.append(fallback)
	return out


def first_title(row, fields):
	return next((row.get(f) for f in fields if row.get(f)), None)
