"""End-to-end walk of FoLT's procurement workflow, Scenario 1: competitive bidding.

Each step of the written workflow, done by the person the workflow names, through the procurement
edge the FoLT app uses (procurement_portal), with the rule that step rests on tried and refused:

  Initiation  The Program Department's requisition (budget + activity concept note) is approved by
              the Head of Programs and then the Head of Finance. An RFQ cannot be sent without one.
  Step 1      The RFQ goes to pre-qualified suppliers only.
  Step 2      Quotations come back within the submission period.
  Step 3      The committee is drawn at random by the system: never the officer who requested the
              quotations, always one Program Department member. Each member scores their own rows;
              the recommendation (Intent to Award) goes to the Head of Finance.
  Step 4      On that approval the LPO is raised from the winning bid and approved.
  Step 5      The service is delivered and received; the supplier's invoice is reviewed by the
              Finance Assistant for accuracy and completeness and forwarded for approval -- the
              reviewer never approves it.

The documents are left on the site afterwards as a worked example (look for "E2E SOP"); the next
run removes them first. Needs the test logins from seed_test_users.py. Run with

    bench --site <site> execute folt_customizations.procurement_sop_e2e.run
"""

import frappe
from frappe.utils import add_days, add_to_date, flt, now_datetime, nowdate

from folt_customizations import committee
from folt_customizations import procurement_portal as pp
from folt_customizations.procurement_e2e_support import attach_supplier_invoice

COMPANY = "FRIENDS of LAKE TURKANA"
MARK = "E2E SOP"
ITEM = "E2E SOP Field Vehicle Hire"
BIDDERS = {"E2E SOP Alpha Motors": 42000, "E2E SOP Beta Tours": 38500, "E2E SOP Gamma Hire": 45000}
LAPSED = "E2E SOP Lapsed Hire"
RFQ_SUBJECT = f"{MARK} vehicle hire for the Loiyangalani field visit"

REQUESTER = "requester.test@folt.test"  # Employee, Programs Department
HOP = "hop.test@folt.test"
HOF = "hof.test@folt.test"
MANAGER = "procmanager.test@folt.test"  # sends the RFQ
BUYER = "purchaser.test@folt.test"
FIN_MANAGER = "finmanager.test@folt.test"
RECEIVER = "receiver.test@folt.test"
FIN_ASSISTANT = "finassistant.test@folt.test"
FIN_OFFICER = "finofficer.test@folt.test"
PROGRAM_MEMBER = "progcommittee.test@folt.test"
# Made here: the procurement officer who requests the quotations also holds the committee role, so
# the draw has a real conflict of interest to keep out; and a finance user who could both review
# and approve an invoice, so the separation between the two is tested rather than assumed.
OFFICER = "e2e.sop.officer@folt.test"
DUAL = "e2e.sop.reviewer@folt.test"

PASS, FAIL = [], []


def check(label, condition, detail=""):
	(PASS if condition else FAIL).append(label)
	print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  — ' + str(detail) if detail else ''}")


def expect_throw(label, fn, exc=Exception):
	try:
		fn()
	except exc as e:
		frappe.clear_last_message()
		check(label, True, frappe.utils.strip_html(str(e)).strip()[:110].replace("\n", " ") or type(e).__name__)
		return
	check(label, False, "no error raised")


def as_user(user, fn):
	previous = frappe.session.user
	frappe.set_user(user)
	try:
		return fn()
	finally:
		frappe.set_user(previous)


def _clean(made, drop=("name", "__islocal", "__unsaved", "docstatus", "owner", "creation", "modified", "workflow_state")):
	doc = {k: v for k, v in made.items() if k not in drop}
	for key, value in list(doc.items()):
		if isinstance(value, list):
			doc[key] = [
				{k: v for k, v in row.items() if k not in ("name", "parent", "parenttype", "parentfield", "docstatus", "idx")}
				if isinstance(row, dict)
				else row
				for row in value
			]
	return doc


# --- set-up and teardown ---------------------------------------------------------------------


def _drop(doctype, name):
	doc = frappe.get_doc(doctype, name)
	if doc.docstatus == 1:
		doc.flags.ignore_permissions = True
		doc.cancel()
	frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)


def teardown():
	frappe.set_user("Administrator")
	suppliers = [*BIDDERS, LAPSED]
	rfqs = frappe.get_all("Request for Quotation", filters={"subject": ["like", f"{MARK}%"]}, pluck="name")
	for pe in frappe.get_all("Payment Entry", filters={"party_type": "Supplier", "party": ["in", suppliers]}, pluck="name"):
		_drop("Payment Entry", pe)
	for doctype in ("Purchase Invoice", "Purchase Receipt", "Purchase Order"):
		for name in frappe.get_all(doctype, filters={"supplier": ["in", suppliers]}, pluck="name", order_by="creation desc"):
			_drop(doctype, name)
	for ev in frappe.get_all("Procurement Committee Evaluation", filters={"request_for_quotation": ["in", rfqs or [""]]}, pluck="name"):
		_drop("Procurement Committee Evaluation", ev)
	for sq in frappe.get_all("Supplier Quotation", filters={"supplier": ["in", suppliers]}, pluck="name"):
		_drop("Supplier Quotation", sq)
	for rfq in rfqs:
		_drop("Request for Quotation", rfq)
	for mr in frappe.get_all("Material Request", filters={"title": ["like", f"{MARK}%"]}, pluck="name", order_by="creation desc"):
		_drop("Material Request", mr)
	for supplier in suppliers:
		if frappe.db.exists("Supplier", supplier):
			frappe.delete_doc("Supplier", supplier, force=True, ignore_permissions=True)
	for user in (OFFICER, DUAL):
		for name in frappe.get_all("Notification Log", filters={"for_user": user}, pluck="name"):
			frappe.delete_doc("Notification Log", name, force=True, ignore_permissions=True)
		if frappe.db.exists("User", user):
			frappe.delete_doc("User", user, force=True, ignore_permissions=True)
	frappe.db.commit()


def _user(email, first_name, roles):
	user = frappe.get_doc({"doctype": "User", "email": email, "first_name": first_name, "send_welcome_email": 0, "user_type": "System User"})
	for role in roles:
		user.append("roles", {"role": role})
	return user.insert(ignore_permissions=True).name


def setup():
	missing = [u for u in (REQUESTER, HOP, HOF, MANAGER, BUYER, FIN_MANAGER, RECEIVER, FIN_ASSISTANT, FIN_OFFICER, PROGRAM_MEMBER) if not frappe.db.exists("User", u)]
	if missing:
		frappe.throw(f"Run seed_test_users.py first: missing {', '.join(missing)}")
	if not frappe.db.exists("Item", ITEM):
		frappe.get_doc({"doctype": "Item", "item_code": ITEM, "item_name": ITEM, "item_group": "Services", "stock_uom": "Nos", "is_stock_item": 0, "is_purchase_item": 1}).insert(ignore_permissions=True)
	group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
	for supplier in (*BIDDERS, LAPSED):
		frappe.get_doc({"doctype": "Supplier", "supplier_name": supplier, "supplier_group": group, "country": "Kenya"}).insert(ignore_permissions=True)
	frappe.db.set_value("Supplier", LAPSED, "folt_qualified_until", add_days(nowdate(), -30))
	_user(OFFICER, "E2E SOP Procurement Officer", ["Purchase User", "Procurement Committee"])
	_user(DUAL, "E2E SOP Reviewer", ["Finance Assistant", "Accounts User", "Accounts Manager"])
	frappe.db.commit()


# --- the walk --------------------------------------------------------------------------------


def run():
	PASS.clear()
	FAIL.clear()
	print("\nFoLT procurement workflow — Scenario 1: competitive bidding\n")
	teardown()
	setup()
	try:
		mr = initiation()
		rfq = step1_rfq(mr)
		bids = step2_quotations(rfq)
		evaluation, winner = step3_committee(rfq, bids)
		order = step4_lpo(evaluation, winner)
		step5_delivery_and_invoice(order)
	finally:
		frappe.set_user("Administrator")
		frappe.db.commit()
	print(f"\n  {len(PASS)} passed, {len(FAIL)} failed")
	if FAIL:
		for label in FAIL:
			print(f"    FAIL  {label}")
	return {"passed": len(PASS), "failed": len(FAIL), "failures": FAIL}


def initiation():
	print("--- initiation: the Program Department's requisition, approved by HoP then HoF ---")
	schedule = add_days(nowdate(), 21)
	draft = {
		"title": f"{MARK} field vehicle hire",
		"company": COMPANY,
		"transaction_date": nowdate(),
		"schedule_date": schedule,
		"folt_priority": "High",
		"folt_justification": "Approved budget line and activity concept note: 5-day field visit to Loiyangalani; one 4x4 with driver.",
		"items": [{"item_code": ITEM, "qty": 5, "uom": "Nos", "folt_estimated_rate": 45000, "schedule_date": schedule}],
	}
	name = as_user(REQUESTER, lambda: pp.save("Material Request", draft))["name"]
	as_user(REQUESTER, lambda: pp.act("Material Request", name, "transition", "Submit for Review"))
	check("the requisition waits on the Head of Programs", frappe.db.get_value("Material Request", name, "workflow_state") == "Pending Head of Programs")
	as_user(HOP, lambda: pp.act("Material Request", name, "transition", "Approve"))
	check("then on the Head of Finance", frappe.db.get_value("Material Request", name, "workflow_state") == "Pending Head of Finance")

	# Not yet approved: an RFQ pointing at it is refused when it is sent.
	early = frappe.get_doc(
		{
			"doctype": "Request for Quotation",
			"company": COMPANY,
			"transaction_date": nowdate(),
			"subject": f"{MARK} premature RFQ",
			"message_for_supplier": "Please quote.",
			"suppliers": [{"supplier": next(iter(BIDDERS)), "send_email": 0}],
			"items": [
				{
					"item_code": ITEM, "qty": 5, "uom": "Nos", "conversion_factor": 1, "schedule_date": schedule,
					"material_request": name,
					"material_request_item": frappe.db.get_value("Material Request Item", {"parent": name}, "name"),
				}
			],
		}
	).insert(ignore_permissions=True)
	expect_throw("an RFQ cannot be sent before the Head of Finance approves the requisition", lambda: as_user(MANAGER, lambda: pp.act("Request for Quotation", early.name, "submit")))
	early.items[0].material_request = None
	early.items[0].material_request_item = None
	early.save(ignore_permissions=True)
	expect_throw("nor on lines that come from no requisition at all", lambda: as_user(MANAGER, lambda: pp.act("Request for Quotation", early.name, "submit")))
	frappe.delete_doc("Request for Quotation", early.name, force=True, ignore_permissions=True)

	as_user(HOF, lambda: pp.act("Material Request", name, "transition", "Approve"))
	mr = frappe.get_doc("Material Request", name)
	check("the Head of Finance's approval initiates procurement", mr.docstatus == 1 and mr.workflow_state == "Approved", mr.workflow_state)
	return name


def step1_rfq(mr):
	print("\n--- step 1: request for quotations to pre-qualified suppliers ---")
	made = as_user(OFFICER, lambda: pp.lookup("erpnext.stock.doctype.material_request.material_request.make_request_for_quotation", {"source_name": mr}))
	rfq_doc = _clean(made)
	rfq_doc.update(
		{
			"subject": RFQ_SUBJECT,
			"message_for_supplier": "Please quote for a 4x4 with driver, 5 days, fuel included.",
			"company": COMPANY,
			"transaction_date": nowdate(),
			"folt_quotation_deadline": str(add_to_date(now_datetime(), days=3)),
			"suppliers": [{"supplier": s, "send_email": 0} for s in (*BIDDERS, LAPSED)],
		}
	)
	rfq = as_user(OFFICER, lambda: pp.save("Request for Quotation", rfq_doc))["name"]
	check("the procurement officer raises the RFQ from the approved requisition", frappe.db.get_value("Request for Quotation Item", {"parent": rfq}, "material_request") == mr)
	expect_throw("a supplier whose pre-qualification has lapsed is not sent it", lambda: as_user(MANAGER, lambda: pp.act("Request for Quotation", rfq, "submit")))
	doc = frappe.get_doc("Request for Quotation", rfq)
	keep = {
		"suppliers": [{k: v for k, v in row.as_dict().items() if k in ("supplier", "send_email")} for row in doc.suppliers if row.supplier != LAPSED],
		"modified": str(doc.modified),
	}
	as_user(OFFICER, lambda: pp.save("Request for Quotation", keep, rfq))
	as_user(MANAGER, lambda: pp.act("Request for Quotation", rfq, "submit"))
	check("sent to the pre-qualified suppliers", frappe.db.get_value("Request for Quotation", rfq, "docstatus") == 1
	      and sorted(frappe.get_all("Request for Quotation Supplier", filters={"parent": rfq}, pluck="supplier")) == sorted(BIDDERS))
	return rfq


def step2_quotations(rfq):
	print("\n--- step 2: quotations received within the submission period ---")
	bids = {}
	for supplier, rate in BIDDERS.items():
		got = as_user(BUYER, lambda: pp.lookup("erpnext.buying.doctype.request_for_quotation.request_for_quotation.make_supplier_quotation_from_rfq", {"source_name": rfq, "for_supplier": supplier}))
		bid = _clean(got)
		bid["items"] = [{**row, "rate": rate} for row in bid["items"]]
		bid.update({"transaction_date": nowdate(), "valid_till": add_days(nowdate(), 30)})
		name = as_user(BUYER, lambda: pp.save("Supplier Quotation", bid))["name"]
		as_user(BUYER, lambda: pp.act("Supplier Quotation", name, "submit"))
		bids[supplier] = name
	check("three quotations are received against the RFQ", len(bids) == 3 and all(frappe.db.get_value("Supplier Quotation", q, "docstatus") == 1 for q in bids.values()))
	late = {**bid, "supplier": next(iter(BIDDERS)), "transaction_date": add_days(nowdate(), 5)}
	expect_throw("a quotation after the submission period is refused", lambda: as_user(BUYER, lambda: pp.save("Supplier Quotation", late)))
	return bids


def step3_committee(rfq, bids):
	print("\n--- step 3: evaluation by a randomly constituted committee, Intent to Award to HoF ---")
	cheapest = min(BIDDERS, key=BIDDERS.get)
	ev = as_user(OFFICER, lambda: pp.handoff("Supplier Quotation", bids[cheapest], "Procurement Committee Evaluation"))["name"]
	doc = frappe.get_doc("Procurement Committee Evaluation", ev)
	check("the evaluation names the officer who requested the quotations", doc.requested_by == OFFICER, doc.requested_by)
	check("and the committee is not chosen at this point", not doc.members)

	expect_throw(
		"nobody chooses the committee by hand",
		lambda: as_user(OFFICER, lambda: pp.save("Procurement Committee Evaluation", {"members": [{"member": PROGRAM_MEMBER}], "modified": str(doc.modified)}, ev)),
	)

	# The draw, dry-run on unsaved copies: always valid, and not always the same committee.
	def dry_draws():
		seen = set()
		for _ in range(25):
			trial = frappe.get_doc("Procurement Committee Evaluation", ev)
			chosen = committee.draw(trial)
			committee.check_composition(trial)
			seen.add(tuple(sorted(chosen)))
		return seen

	pool = committee.eligible_members(committee.excluded_for(frappe._dict(requested_by=OFFICER, request_for_quotation=rfq)))
	seen = as_user(OFFICER, dry_draws)
	check("every draw seats a Program Department member and never the officer", all(PROGRAM_MEMBER in c or committee.program_representatives(c) for c in seen) and not any(OFFICER in c for c in seen))
	if len(pool) > committee.committee_size():
		check("the draw is random: different committees come out of the same pool", len(seen) > 1, f"{len(seen)} distinct committees in 25 draws from {len(pool)} eligible")

	as_user(OFFICER, lambda: pp.act("Procurement Committee Evaluation", ev, "transition", "Send to Committee"))
	doc = frappe.get_doc("Procurement Committee Evaluation", ev)
	members = [row.member for row in doc.members]
	check("sending it to the committee draws the committee", doc.workflow_state == "Committee Reviewing" and len(members) == committee.committee_size(), members)
	check("the draw is recorded: how, when and from whom", doc.folt_constitution == committee.RANDOM_DRAW and doc.folt_drawn_on and OFFICER not in (doc.folt_draw_pool or ""), doc.folt_draw_pool)
	check("the officer who requested the quotations is not on it", OFFICER not in members)
	check("at least one member represents the Program Department", bool(committee.program_representatives(members)), sorted(committee.program_representatives(members)))
	check("every member has a row against every quotation", len(doc.quotation_scores) == len(members) * len(bids))

	def drop_colleague():
		d = frappe.get_doc("Procurement Committee Evaluation", ev)
		d.set("members", d.members[:1])
		d.save()

	expect_throw("a member cannot change who sits on the committee", lambda: as_user(members[0], drop_colleague))

	def score_as(member):
		d = frappe.get_doc("Procurement Committee Evaluation", ev)
		for row in d.quotation_scores:
			if row.member == member:
				row.score = 9 if row.supplier_quotation == bids[cheapest] else 6
				row.comments = "Lowest total; vehicle and driver meet the specification." if row.supplier_quotation == bids[cheapest] else "Meets the specification at a higher price."
		for row in d.members:
			if row.member == member:
				row.reviewed = 1
		d.save()

	def score_for_somebody_else():
		d = frappe.get_doc("Procurement Committee Evaluation", ev)
		for row in d.quotation_scores:
			if row.member == members[1]:
				row.score = 1
		d.save()

	expect_throw("a member cannot score in another member's name", lambda: as_user(members[0], score_for_somebody_else))
	for member in members:
		as_user(member, lambda: score_as(member))

	def recommend():
		d = frappe.get_doc("Procurement Committee Evaluation", ev)
		d.recommended_supplier_quotation = bids[cheapest]
		d.recommendation_notes = "Intent to Award: lowest evaluated bid meeting the technical specification, delivery timeline and compliance requirements."
		d.save()

	as_user(members[0], recommend)
	as_user(members[0], lambda: pp.act("Procurement Committee Evaluation", ev, "transition", "Submit for Award Approval"))
	check("the recommendation (Intent to Award) goes to the Head of Finance", frappe.db.get_value("Procurement Committee Evaluation", ev, "workflow_state") == "Pending Head of Finance Approval")
	expect_throw("nobody else approves the award", lambda: as_user(BUYER, lambda: pp.act("Procurement Committee Evaluation", ev, "transition", "Approve (Intent to Award)")))
	as_user(HOF, lambda: pp.act("Procurement Committee Evaluation", ev, "transition", "Approve (Intent to Award)"))
	doc = frappe.get_doc("Procurement Committee Evaluation", ev)
	check("the Head of Finance approves the award", doc.workflow_state == "Approved" and doc.recommended_supplier == cheapest, f"{doc.workflow_state} {doc.recommended_supplier}")
	return ev, bids[cheapest]


def step4_lpo(evaluation, winner):
	print("\n--- step 4: the LPO, issued on the approved award ---")
	po = as_user(BUYER, lambda: pp.handoff("Procurement Committee Evaluation", evaluation, "Purchase Order"))["name"]
	order = frappe.get_doc("Purchase Order", po)
	check("the LPO is raised from the winning bid", order.folt_committee_evaluation == evaluation and order.items[0].supplier_quotation == winner)
	as_user(BUYER, lambda: pp.act("Purchase Order", po, "transition", "Submit for approval"))
	tier = frappe.db.get_value("Purchase Order", po, "folt_approval_tier")
	as_user(FIN_MANAGER, lambda: pp.act("Purchase Order", po, "transition", "Approve"))
	if tier == "Executive":
		as_user("ed.test@folt.test", lambda: pp.act("Purchase Order", po, "transition", "Approve"))
	check("the LPO is approved and issued", frappe.db.get_value("Purchase Order", po, ["workflow_state", "docstatus"]) == ("Approved", 1), tier)
	return po


def step5_delivery_and_invoice(order):
	print("\n--- step 5: delivery, and the invoice verified by the Finance Assistant ---")
	made = as_user(RECEIVER, lambda: pp.lookup("erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt", {"source_name": order}))
	receipt = _clean(made)
	receipt.update({"posting_date": nowdate(), "supplier_delivery_note": "SOP-DN-001"})
	pr = as_user(RECEIVER, lambda: pp.save("Purchase Receipt", receipt))["name"]
	as_user(RECEIVER, lambda: pp.act("Purchase Receipt", pr, "submit"))
	check("the service is received against the LPO", flt(frappe.db.get_value("Purchase Order", order, "per_received")) == 100)

	made = as_user(FIN_ASSISTANT, lambda: pp.lookup("erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_invoice", {"source_name": order}))
	draft = _clean(made)
	draft.update({"posting_date": nowdate(), "set_posting_time": 1})
	pi = as_user(FIN_ASSISTANT, lambda: pp.save("Purchase Invoice", draft))["name"]
	expect_throw("an invoice with no supplier invoice number, date or copy cannot be forwarded", lambda: as_user(FIN_ASSISTANT, lambda: pp.act("Purchase Invoice", pi, "transition", "Submit for approval")))
	as_user(FIN_ASSISTANT, lambda: pp.save("Purchase Invoice", {"bill_no": "BT-2026-0117", "bill_date": nowdate(), "modified": str(frappe.db.get_value("Purchase Invoice", pi, "modified"))}, pi))
	attach_supplier_invoice(pi)
	check("the invoice matches the LPO and the receipt", frappe.db.get_value("Purchase Invoice", pi, "folt_match_status") == "Matched", frappe.db.get_value("Purchase Invoice", pi, "folt_match_notes"))
	expect_throw("only the Finance Assistant forwards it for approval", lambda: as_user(FIN_OFFICER, lambda: pp.act("Purchase Invoice", pi, "transition", "Submit for approval")))

	# A reviewer who also holds approval rights still may not approve what they reviewed.
	as_user(DUAL, lambda: pp.act("Purchase Invoice", pi, "transition", "Submit for approval"))
	check("forwarding records who reviewed it", frappe.db.get_value("Purchase Invoice", pi, "folt_reviewed_by") == DUAL)
	expect_throw("the reviewer cannot approve it", lambda: as_user(DUAL, lambda: pp.act("Purchase Invoice", pi, "transition", "Approve")))
	as_user(HOF, lambda: pp.act("Purchase Invoice", pi, "transition", "Reject", "Please have the Finance Assistant on duty review this one."))
	check("returned, it comes back for a fresh review", frappe.db.get_value("Purchase Invoice", pi, ["workflow_state", "folt_reviewed_by"]) == ("Draft", None))

	as_user(FIN_ASSISTANT, lambda: pp.act("Purchase Invoice", pi, "transition", "Submit for approval"))
	check("the Finance Assistant reviews and forwards it", frappe.db.get_value("Purchase Invoice", pi, ["workflow_state", "folt_reviewed_by"]) == ("Pending Approval", FIN_ASSISTANT))
	as_user(HOF, lambda: pp.act("Purchase Invoice", pi, "transition", "Approve"))
	inv = frappe.get_doc("Purchase Invoice", pi)
	check("and it is approved and posted for payment", inv.docstatus == 1 and inv.workflow_state == "Approved" and flt(inv.outstanding_amount) > 0, f"{inv.workflow_state} {inv.outstanding_amount}")
