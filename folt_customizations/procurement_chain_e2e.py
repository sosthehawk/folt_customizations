"""End-to-end check of the procurement chain: a submitted bid through to an authorised order.

What this proves, in the order the routes take:

  competitive bidding   a submitted bid says it is one of N in a competition, hands off to one
                        Procurement Committee Evaluation per RFQ, and -- only once the committee
                        has scored, named a winner and had the award approved -- hands off to a
                        Purchase Order mapped from the winning bid.
  single sourcing       a bid that was never competed refuses the committee route and hands off
                        to a Derogation / Waiver Request prefilled from it, which authorises an
                        order once the Executive Director has signed it.
  the gate              an order that carries neither an approved award nor an approved waiver
                        cannot be sent for approval at all, and one that carries an award cannot
                        be redirected to a supplier the award does not name.

Idempotent: tears down its own fixtures first. Run with

    bench --site <site> execute folt_customizations.procurement_chain_e2e.run
"""

import frappe
from frappe.model.workflow import apply_workflow
from frappe.utils import add_days, flt, nowdate

from folt_customizations import document_guide, procurement_chain
from folt_customizations.procurement import AUTHORISED, EVALUATION_DOCTYPE, WAIVER_DOCTYPE

ITEM = "E2E Route Service"
# The competition: two bidders in the register's ICT category, cheapest second so the award is
# never simply the first row.
ALPHA, BETA = "E2E Route Alpha", "E2E Route Beta"
BIDS = {ALPHA: 400_000, BETA: 250_000}
# The single-source supplier, pre-qualified for a DIFFERENT category on purpose: an order to them
# under ICT is exactly the order only a waiver can license.
SOLE = "E2E Route Sole"
SOLE_BID = 180_000

SUPPLIERS = {ALPHA: "ICT", BETA: "ICT", SOLE: "Catering"}
BUYER = "e2e.route.buyer@folt.test"
MEMBERS = ("e2e.route.member1@folt.test", "e2e.route.member2@folt.test")

PASS, FAIL = [], []


def check(label, condition, detail=""):
	(PASS if condition else FAIL).append(label)
	print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")


def expect_throw(label, fn):
	try:
		fn()
	except frappe.ValidationError as e:
		check(label, True, str(e)[:100].replace("\n", " "))
		return
	except Exception as e:  # noqa: BLE001
		check(label, False, f"wrong error type: {type(e).__name__}: {e}")
		return
	check(label, False, "no error raised")


# --- fixtures --------------------------------------------------------------------------------


def teardown():
	for doctype in ("Purchase Order", EVALUATION_DOCTYPE, WAIVER_DOCTYPE, "Supplier Quotation", "Request for Quotation"):
		for name in frappe.get_all(doctype, filters={"docstatus": ["<", 2]}, pluck="name"):
			doc = frappe.get_doc(doctype, name)
			if not _is_ours(doc):
				continue
			if doc.docstatus == 1:
				doc.cancel()
			doc.delete(force=True)

	for supplier in SUPPLIERS:
		if frappe.db.exists("Supplier", supplier):
			frappe.delete_doc("Supplier", supplier, force=True, ignore_permissions=True)
	if frappe.db.exists("Item", ITEM):
		frappe.delete_doc("Item", ITEM, force=True, ignore_permissions=True)

	for user in (BUYER, *MEMBERS):
		for name in frappe.get_all("Notification Log", filters={"for_user": user}, pluck="name"):
			frappe.delete_doc("Notification Log", name, force=True, ignore_permissions=True)
		if frappe.db.exists("User", user):
			frappe.delete_doc("User", user, force=True, ignore_permissions=True)

	frappe.db.commit()


def _is_ours(doc) -> bool:
	"""Whether a document belongs to this run's fixtures.

	Every branch has to name this script's own item or suppliers, and that is not fussiness:
	teardown deletes what it matches, and this runs on whatever site somebody points it at. An
	evaluation is matched through its RFQ's items rather than by having an RFQ at all -- the
	evaluation and the waiver carry no item table of their own, and "any evaluation of any
	competition" would have this script deleting FoLT's real procurement decisions.
	"""
	if doc.meta.has_field("items"):
		return any(row.get("item_code") == ITEM for row in doc.items)

	if doc.get("supplier") in SUPPLIERS:
		return True

	rfq = doc.get("request_for_quotation")
	return bool(rfq) and bool(
		frappe.db.exists("Request for Quotation Item", {"parent": rfq, "item_code": ITEM})
	)


def make_user(email, first_name, roles):
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": first_name,
			"send_welcome_email": 0,
			"user_type": "System User",
			"roles": [{"role": role} for role in roles],
		}
	)
	user.flags.ignore_permissions = True
	return user.insert().name


def make_fixtures(company):
	frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": ITEM,
			"item_name": ITEM,
			"item_group": "Services",
			"stock_uom": "Nos",
			"is_stock_item": 0,
		}
	).insert(ignore_permissions=True)

	for supplier, group in SUPPLIERS.items():
		frappe.get_doc(
			{"doctype": "Supplier", "supplier_name": supplier, "supplier_group": group}
		).insert(ignore_permissions=True)

	make_user(BUYER, "E2E Route Buyer", ["Purchase User"])
	for index, member in enumerate(MEMBERS, start=1):
		make_user(member, f"E2E Route Member {index}", ["Procurement Committee"])


def make_rfq(company, suppliers):
	doc = frappe.get_doc(
		{
			"doctype": "Request for Quotation",
			"company": company,
			"transaction_date": nowdate(),
			"schedule_date": add_days(nowdate(), 14),
			"subject": "E2E procurement route",
			"message_for_supplier": "E2E: please quote.",
			"suppliers": [{"supplier": supplier} for supplier in suppliers],
			"items": [_line(qty=1, schedule=True)],
		}
	)
	doc.insert(ignore_permissions=True)
	doc.submit()
	return doc


def make_quotation(company, supplier, rate, rfq=None):
	line = _line(qty=1, schedule=True)
	line["rate"] = rate
	if rfq:
		line["request_for_quotation"] = rfq.name
		line["request_for_quotation_item"] = rfq.items[0].name

	doc = frappe.get_doc(
		{
			"doctype": "Supplier Quotation",
			"supplier": supplier,
			"company": company,
			"transaction_date": nowdate(),
			"valid_till": add_days(nowdate(), 30),
			"items": [line],
		}
	)
	doc.insert(ignore_permissions=True)
	doc.submit()
	return doc


def make_order(company, supplier, group, authority=None):
	"""A Purchase Order keyed by hand, as a buyer would raise one -- optionally with an authority."""
	order = frappe.get_doc(
		{
			"doctype": "Purchase Order",
			"supplier": supplier,
			"company": company,
			"transaction_date": nowdate(),
			"schedule_date": add_days(nowdate(), 14),
			"folt_supplier_group": group,
			"items": [dict(_line(qty=1, schedule=True), rate=SOLE_BID)],
			**(authority or {}),
		}
	)
	return order.insert(ignore_permissions=True)


def _line(qty, schedule=False):
	line = {
		"item_code": ITEM,
		"item_name": ITEM,
		"description": ITEM,
		"uom": "Nos",
		"conversion_factor": 1,
		"qty": qty,
	}
	if schedule:
		line["schedule_date"] = add_days(nowdate(), 14)
	return line


# --- the routes -------------------------------------------------------------------------------


def award(evaluation, winner):
	"""Take an evaluation the whole way: to the committee, scored, awarded, approved.

	Run as whoever is executing the script (Administrator in practice), which is the one session
	`enforce_self_scoring` and `enforce_committee_composition` exempt -- see their docstrings.
	Those two rules are `committee_evaluation_e2e`'s subject, not this script's; here the point
	is only that the award exists before an order can be raised on it.
	"""
	# Started from wherever it is: the checks below reach `Committee Reviewing` on their own way
	# to proving that award approval cannot be sought with no winner named.
	if evaluation.workflow_state == "Draft":
		evaluation = apply_workflow(evaluation, "Send to Committee")

	for row in evaluation.members:
		row.reviewed = 1
	for row in evaluation.quotation_scores:
		row.score = 8 if row.supplier_quotation == winner else 5
	evaluation.recommended_supplier_quotation = winner
	evaluation.save(ignore_permissions=True)

	evaluation = apply_workflow(evaluation, "Submit for Award Approval")
	return apply_workflow(evaluation, "Approve (Intent to Award)")


def authorise(waiver):
	"""Take a waiver from Draft to authorised, writing the justification the preparer owes."""
	waiver.reasons = "E2E: sole capable supplier for the live-stream engagement."
	waiver.risks_created = "E2E: no price benchmark."
	waiver.mitigating_actions = "E2E: rate compared against last year's engagement."
	waiver.period_of_derogation = "E2E: one engagement."
	waiver.save(ignore_permissions=True)

	waiver = apply_workflow(waiver, "Submit for Finance Review")
	waiver = apply_workflow(waiver, "Review & Forward")
	return apply_workflow(waiver, "Authorise")


# --- checks ------------------------------------------------------------------------------------


def _a_project():
	"""Any project on the site, for the heading check. None if this site has none."""
	names = frappe.get_all("Project", pluck="name", limit=1)
	return names[0] if names else None


def _project_name():
	return frappe.db.get_value("Project", _a_project(), "project_name")


def _offers(guide, label) -> bool:
	"""Whether the guide payload carries a ready hand-off by that name."""
	return any(row["label"] == label and row["ready"] for row in guide.get("handoffs") or [])


def run():
	print("\nProcurement chain — submitted bid -> committee award or authorised waiver -> order\n")
	teardown()

	company = frappe.defaults.get_user_default("Company") or frappe.get_all("Company", pluck="name")[0]
	make_fixtures(company)

	check(
		"the order carries a field for each authority",
		all(
			frappe.get_meta("Purchase Order").has_field(field)
			for field in ("folt_committee_evaluation", "folt_waiver_request")
		),
	)

	print("competitive bidding")
	rfq = make_rfq(company, (ALPHA, BETA))
	alpha = make_quotation(company, ALPHA, BIDS[ALPHA], rfq=rfq)
	beta = make_quotation(company, BETA, BIDS[BETA], rfq=rfq)

	route = procurement_chain.get_route("Supplier Quotation", alpha.name)
	check(
		"a submitted bid says which competition it is part of",
		"Competitive bidding" in (route["note"] or "") and rfq.name in route["note"],
		route["note"],
	)
	offered = {row["label"]: row for row in route["handoffs"]}
	check(
		"and offers the committee evaluation",
		offered["Committee Evaluation"]["ready"],
		f"ready_at {offered['Committee Evaluation']['ready_at']}",
	)

	created = procurement_chain.make_committee_evaluation(alpha.name)
	evaluation = frappe.get_doc(EVALUATION_DOCTYPE, created["name"])
	check(
		"the evaluation is raised on the RFQ, not on the one bid",
		evaluation.request_for_quotation == rfq.name and created["bids"] == 2,
		f"{evaluation.name}: {created['bids']} bids on {evaluation.request_for_quotation}",
	)
	check(
		"and carries the requester the conflict-of-interest rule turns on",
		evaluation.requested_by == rfq.owner,
		evaluation.requested_by,
	)

	# Who sits on the committee is the one thing the hand-off does not copy, because there is
	# nothing on a bid to copy it from -- so the buyer names them, and the grid follows.
	evaluation.extend("members", [{"member": member} for member in MEMBERS])
	evaluation.save(ignore_permissions=True)
	check(
		"naming the committee gives every member a row against every bid",
		len(evaluation.quotation_scores) == len(MEMBERS) * 2,
		f"{len(evaluation.quotation_scores)} rows",
	)

	expect_throw(
		"a second bid on the same RFQ cannot open a second evaluation",
		lambda: procurement_chain.make_committee_evaluation(beta.name),
	)
	check(
		"and the other bidder's form reports the evaluation instead of offering one",
		procurement_chain.get_route("Supplier Quotation", beta.name)["handoffs"][0]["existing"]
		== [evaluation.name]
		and not procurement_chain.get_route("Supplier Quotation", beta.name)["handoffs"][0]["ready"],
	)

	expect_throw(
		"no order can be raised while the committee is still deciding",
		lambda: procurement_chain.make_award_order(evaluation.name),
	)

	# An award naming a bid from outside this competition is not an award of it.
	stray = make_quotation(company, SOLE, SOLE_BID)
	evaluation.reload()
	evaluation.recommended_supplier_quotation = stray.name
	expect_throw(
		"the award cannot name a bid from another competition",
		lambda: evaluation.save(ignore_permissions=True),
	)

	evaluation.reload()
	evaluation = apply_workflow(evaluation, "Send to Committee")
	for row in evaluation.members:
		row.reviewed = 1
	evaluation.save(ignore_permissions=True)
	expect_throw(
		"and award approval cannot be sought with no winner named",
		lambda: apply_workflow(evaluation, "Submit for Award Approval"),
	)

	evaluation.reload()
	awarded = award(evaluation, beta.name)
	check(
		"the approved award names the winning bid's supplier, derived from the bid",
		awarded.workflow_state == AUTHORISED and awarded.recommended_supplier == BETA,
		f"{awarded.workflow_state}, {awarded.recommended_supplier}",
	)

	# How the Desk gets the button. The evaluation and the waiver are workflow documents, so
	# their hand-offs travel inside the guide payload folt_guide.js already renders -- the
	# Supplier Quotation's own script is only needed for the one document with no workflow.
	check(
		"the approved award offers its order through the document guide",
		_offers(document_guide.get_guide(EVALUATION_DOCTYPE, awarded.name), "Purchase Order"),
		str([row["label"] for row in document_guide.get_guide(EVALUATION_DOCTYPE, awarded.name)["handoffs"]]),
	)

	ordered = procurement_chain.make_award_order(awarded.name)
	order = frappe.get_doc("Purchase Order", ordered["name"])
	check(
		"the order is mapped from the winning bid and carries the award that authorised it",
		order.folt_committee_evaluation == awarded.name
		and order.supplier == BETA
		and order.items[0].supplier_quotation == beta.name
		and flt(order.grand_total) == flt(BIDS[BETA]),
		f"{order.name}: {order.supplier}, {order.currency} {order.grand_total}, from {order.items[0].supplier_quotation}",
	)
	check(
		"with the Required By date the RFQ asked the suppliers for",
		str(order.schedule_date) == add_days(nowdate(), 14),
		str(order.schedule_date),
	)

	order = apply_workflow(order, "Submit for approval")
	check(
		"and an authorised order goes to the Finance Manager",
		order.workflow_state == "Pending Approval",
		order.workflow_state,
	)

	expect_throw(
		"one award, one order",
		lambda: procurement_chain.make_award_order(awarded.name),
	)

	print("\nsingle sourcing")
	sole = make_quotation(company, SOLE, SOLE_BID)
	route = procurement_chain.get_route("Supplier Quotation", sole.name)
	check(
		"an uncompeted bid says so",
		"Not part of a competition" in (route["note"] or ""),
		route["note"],
	)
	expect_throw(
		"and cannot be taken to the committee",
		lambda: procurement_chain.make_committee_evaluation(sole.name),
	)

	raised = procurement_chain.make_waiver_request(sole.name)
	waiver = frappe.get_doc(WAIVER_DOCTYPE, raised["name"])
	check(
		"the waiver is prefilled from the bid",
		waiver.supplier == SOLE
		and flt(waiver.estimated_value) == flt(SOLE_BID)
		and waiver.supplier_quotation == sole.name
		and ITEM in (waiver.items_service or ""),
		f"{waiver.name}: {waiver.supplier}, {waiver.estimated_value}, {(waiver.items_service or '')[:40]}",
	)
	check(
		"and says what is left to write",
		not waiver.reasons and "Reasons for the Waiver" in raised["to_write"],
		", ".join(raised["to_write"]),
	)
	check(
		"a waiver typed from scratch gets the same heading, from the same function",
		procurement_chain.get_waiver_heading() == company
		and procurement_chain.get_waiver_heading(project=_a_project()) == f"{company} — {_project_name()}",
		f"{procurement_chain.get_waiver_heading()} / {procurement_chain.get_waiver_heading(project=_a_project())}",
	)

	expect_throw(
		"a second waiver cannot be raised on the same bid",
		lambda: procurement_chain.make_waiver_request(sole.name),
	)
	expect_throw(
		"and no order until the waiver is authorised",
		lambda: procurement_chain.make_waiver_order(waiver.name),
	)

	print("\nthe gate on the order")
	unauthorised = make_order(company, ALPHA, "ICT")
	check(
		"an order with no authority can still be prepared in Draft",
		unauthorised.workflow_state == "Draft",
		unauthorised.name,
	)
	expect_throw(
		"but cannot be sent for approval",
		lambda: apply_workflow(unauthorised, "Submit for approval"),
	)

	unauthorised.reload()
	unauthorised.folt_waiver_request = waiver.name
	unauthorised.save(ignore_permissions=True)
	expect_throw(
		"nor on the strength of a waiver nobody has approved yet",
		lambda: apply_workflow(unauthorised, "Submit for approval"),
	)

	expect_throw(
		"and an unapproved waiver does not license an unqualified supplier either",
		lambda: make_order(company, SOLE, "ICT", {"folt_waiver_request": waiver.name}),
	)

	# The probe order above is still carrying the waiver's link, and the hand-off below counts
	# any live order against an authority -- a draft one included, since somebody has already
	# started it. Clear it: that order was raised to test the gate, not to buy anything.
	unauthorised.reload()
	unauthorised.folt_waiver_request = None
	unauthorised.save(ignore_permissions=True)

	authorised = authorise(waiver)
	check(
		"the authorised waiver is the Executive Director's decision",
		authorised.workflow_state == AUTHORISED and authorised.docstatus == 1,
		authorised.workflow_state,
	)

	check(
		"and so does the authorised waiver",
		_offers(document_guide.get_guide(WAIVER_DOCTYPE, authorised.name), "Purchase Order"),
		str([row["label"] for row in document_guide.get_guide(WAIVER_DOCTYPE, authorised.name)["handoffs"]]),
	)

	from_waiver = procurement_chain.make_waiver_order(authorised.name)
	derived = frappe.get_doc("Purchase Order", from_waiver["name"])
	check(
		"the waiver's hand-off derives the order from the bid it was raised on",
		derived.folt_waiver_request == authorised.name
		and derived.items[0].supplier_quotation == sole.name
		and flt(derived.grand_total) == flt(SOLE_BID),
		f"{derived.name}: {derived.currency} {derived.grand_total} from {derived.items[0].supplier_quotation}",
	)
	expect_throw(
		"one waiver, one order",
		lambda: procurement_chain.make_waiver_order(authorised.name),
	)

	# The same authority on an order keyed by hand rather than derived: the gate reads the link
	# and what it links to, not who created the order -- which is what makes it a control on
	# every route to a Purchase Order rather than only on the two buttons. The one-order rule
	# above is the hand-off's protection against a double click; deliberately NOT the gate's,
	# because a framework award legitimately runs to more than one order and only FoLT can say
	# where that line is.
	single_source = make_order(company, SOLE, "ICT", {"folt_waiver_request": authorised.name})
	single_source = apply_workflow(single_source, "Submit for approval")
	check(
		"and licenses a single-source order outside the pre-qualified category",
		single_source.workflow_state == "Pending Approval",
		f"{single_source.name}: {single_source.supplier} under {single_source.folt_supplier_group}",
	)

	# Saved before the transition, not with it: `apply_workflow` calls `load_from_db()` first, so
	# an edit carried into it in memory is silently discarded.
	redirected = frappe.get_doc("Purchase Order", derived.name)
	redirected.folt_waiver_request = None
	redirected.folt_committee_evaluation = awarded.name
	redirected.save(ignore_permissions=True)
	expect_throw(
		"an award cannot be redirected to a supplier it does not name",
		lambda: apply_workflow(redirected, "Submit for approval"),
	)

	frappe.db.commit()
	print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
	if FAIL:
		print("FAILED: " + "; ".join(FAIL))
	return {"passed": len(PASS), "failed": FAIL}
