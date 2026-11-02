"""The purchase requisition: ERPNext's Material Request, purpose Purchase (ADR-008).

A requisition goes Draft -> Pending Head of Programs -> Pending Head of Finance -> Approved, which
submits it (fixture: FoLT Purchase Requisition Approval). Either approver may return it for
revision or reject it, with a reason (workflow_access). What it has been turned into afterwards --
an RFQ, an order, a receipt -- is ERPNext's own: its `status`, `per_ordered` and `per_received`,
and the documents that name it on their lines. Nothing here keeps a second status.

What this adds on save:

*The activity it is for.* A requisition may name an approved Activity Requisition. Its project,
cost centre and accounting dimensions (programme, donor, grant, location) fill every line where the
line has none, and its budget line, company and requester's department fill the requisition where
they are empty. A typed value is never overwritten.

*An estimate, not a price.* Each line carries the requester's estimated unit cost
(folt_estimated_rate) beside ERPNext's own rate, which ERPNext takes from the buying price list
and is free to change. Quantity x estimate is the line's estimated amount; their sum is the
requisition's estimated total -- what the approvers weigh. The price that commits FoLT is the
order's.

*Why.* The justification is required before the requisition leaves Draft.

*The estimate is settled on the requester's save.* ERPNext's price list replaces a typed rate
where it has a price; see _price_list for why that happens on the first save and never later.

*Where it lands.* A stock item with no warehouse goes to Stock Settings' default warehouse.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

# Activity Requisition field -> Material Request Item field, filled where the line is empty.
_LINE_FROM_ACTIVITY = {
	"project": "project",
	"cost_center": "cost_center",
	"program": "program",
	"donor": "donor",
	"grant_project": "grant_project",
	"location": "location",
}


def validate(doc, method=None):
	if doc.get("material_request_type") and doc.material_request_type != "Purchase":
		return
	_title(doc)
	_from_activity(doc)
	_price_list(doc)
	_default_warehouse(doc)
	_estimate(doc)
	_require_justification(doc)


def _title(doc):
	"""A title that says what is being bought. The field's default is a template
	("{material_request_type}"); a title left empty or still templated becomes the items, as
	ERPNext's own set_title does for an empty one."""
	title = (doc.get("title") or "").strip()
	if title and "{" not in title:
		return
	names = [r.get("item_name") or r.get("item_code") for r in doc.get("items") or [] if r.get("item_code")]
	doc.title = ", ".join(dict.fromkeys(names))[:100] or _("Purchase requisition")


def _from_activity(doc):
	name = doc.get("folt_activity_requisition")
	if not name:
		return
	activity = frappe.db.get_value(
		"Activity Requisition", name,
		["docstatus", "workflow_state", "company", "requested_by", "budget_classification", *_LINE_FROM_ACTIVITY],
		as_dict=True,
	)
	if not activity:
		frappe.throw(_("Activity Requisition {0} does not exist.").format(name))
	if activity.docstatus != 1:
		frappe.throw(
			_("Activity Requisition {0} is not approved ({1}). A purchase is raised against an approved activity.").format(
				frappe.bold(name), activity.workflow_state or _("draft")
			),
			title=_("Activity not approved"),
		)
	if activity.company and doc.company and activity.company != doc.company:
		frappe.throw(_("Activity Requisition {0} belongs to {1}, not {2}.").format(name, activity.company, doc.company))
	if not doc.get("folt_budget_line") and activity.budget_classification:
		doc.folt_budget_line = activity.budget_classification
	if not doc.get("folt_department") and activity.requested_by:
		doc.folt_department = frappe.db.get_value("Employee", activity.requested_by, "department")
	for row in doc.get("items") or []:
		for source, target in _LINE_FROM_ACTIVITY.items():
			if activity.get(source) and row.meta.has_field(target) and not row.get(target):
				row.set(target, activity.get(source))


def _price_list(doc):
	"""Settle the estimate on the requester's own save, not halfway through approval.

	ERPNext prices a requisition's lines from its buying price list: wherever an item has a price
	there, that price replaces the typed rate when the list is first set (MaterialRequest.on_update
	-> update_item_rates). It sets the default list only when the person saving may read Price List
	-- a requester may not, an approver may -- so left alone, the Head of Programs' Approve would
	reprice a requisition the requester had already sent. Setting the default list on the first
	save puts ERPNext's pricing where the requester sees it, and no later save changes it."""
	if doc.is_new() and not doc.get("buying_price_list"):
		doc.buying_price_list = frappe.defaults.get_defaults().get("buying_price_list") or None


def _default_warehouse(doc):
	default = None
	for row in doc.get("items") or []:
		if row.get("warehouse") or not row.get("item_code"):
			continue
		if not frappe.get_cached_value("Item", row.item_code, "is_stock_item"):
			continue
		default = default or doc.get("set_warehouse") or frappe.db.get_single_value("Stock Settings", "default_warehouse")
		if default:
			row.warehouse = default


def _estimate(doc):
	"""The requester's estimate, kept apart from ERPNext's price. A line's estimated unit cost is
	what the requester typed (a line saved without one starts from ERPNext's rate); its estimated
	amount is quantity x that; the requisition's estimated total their sum."""
	total = 0.0
	for row in doc.get("items") or []:
		if not row.meta.has_field("folt_estimated_rate"):
			continue
		if row.get("folt_estimated_rate") in (None, "") and flt(row.get("rate")):
			row.folt_estimated_rate = flt(row.rate)
		row.folt_estimated_amount = flt(row.get("qty")) * flt(row.get("folt_estimated_rate"))
		total += row.folt_estimated_amount
	if doc.meta.has_field("folt_estimated_total"):
		doc.folt_estimated_total = total


def _require_justification(doc):
	if not doc.meta.has_field("folt_justification") or not doc.meta.has_field("workflow_state"):
		return
	state = doc.get("workflow_state")
	if state and state != "Draft" and state != "Rejected" and not (doc.get("folt_justification") or "").strip():
		frappe.throw(_("Say why this purchase is needed (Justification) before it goes for review."), title=_("Justification required"))


def preview(doc):
	"""What the save would fill, for the form while it is typed (procurement_portal.preview): the
	activity's values and the estimates. No checks -- the save makes those."""
	if doc.get("material_request_type") and doc.material_request_type != "Purchase":
		return
	_from_activity(doc)
	_estimate(doc)


@frappe.whitelist()
def activity_defaults(activity_requisition):
	"""What an approved Activity Requisition gives a new requisition, for the form to show before
	it is saved (the save fills the same values again, server-side)."""
	activity = frappe.get_doc("Activity Requisition", activity_requisition)
	activity.check_permission("read")
	if activity.docstatus != 1:
		frappe.throw(_("Activity Requisition {0} is not approved.").format(activity_requisition), title=_("Activity not approved"))
	return {
		"company": activity.company,
		"folt_department": frappe.db.get_value("Employee", activity.requested_by, "department") if activity.requested_by else None,
		"folt_budget_line": activity.get("budget_classification"),
		# The activity's date is when the goods are needed -- unless it has already passed, which
		# ERPNext would refuse as a required-by date; then the requester says when.
		"schedule_date": str(activity.activity_date) if activity.get("activity_date") and getdate(activity.activity_date) >= getdate(nowdate()) else None,
		"line": {target: activity.get(source) for source, target in _LINE_FROM_ACTIVITY.items() if activity.get(source)},
		"title": activity.get("activity_program"),
	}
