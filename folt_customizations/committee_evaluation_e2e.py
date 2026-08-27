"""End-to-end check of the Procurement Committee Evaluation scoring grid and its notification.

Covers the three halves of competitive bidding that key off the Request for Quotation:
picking the RFQ gives every committee member a row to score against every bid received,
entering `Committee Reviewing` tells those members -- and only those members -- to go and score,
and once they are scoring each of them can fill in their own row and nobody else's.

Idempotent: tears down its own fixtures first. Run with

    bench --site <site> execute folt_customizations.committee_evaluation_e2e.run
"""

import time

import frappe
from frappe.model.workflow import apply_workflow
from frappe.utils import add_days, nowdate

from folt_customizations.procurement import COMMITTEE_REVIEW_STATE

ITEM = "E2E Bid Service"
SUPPLIERS = ("E2E Alpha Supplies", "E2E Beta Traders", "E2E Gamma Logistics")
BUYER = "e2e.buyer@folt.test"
MEMBERS = ("e2e.member1@folt.test", "e2e.member2@folt.test")
# A holder of the Procurement Committee role who is NOT on this evaluation's committee. Nothing
# about this document is any of their business, and that is what the suppression check proves.
OUTSIDER = "e2e.outsider@folt.test"

# Bids, cheapest last on purpose -- the grid is expected to sort them, not to keep entry order.
BIDS = {SUPPLIERS[0]: 180_000, SUPPLIERS[1]: 120_000}
LATE_BID = {SUPPLIERS[2]: 150_000}

PASS, FAIL = [], []


def check(label, condition, detail=""):
	(PASS if condition else FAIL).append(label)
	print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")


def expect_throw(label, fn):
	try:
		fn()
	except frappe.ValidationError as e:
		check(label, True, str(e)[:90].replace("\n", " "))
		return
	except Exception as e:  # noqa: BLE001
		check(label, False, f"wrong error type: {type(e).__name__}: {e}")
		return
	check(label, False, "no error raised")


# --- fixtures --------------------------------------------------------------------------------


def teardown():
	for name in frappe.get_all("Procurement Committee Evaluation", pluck="name"):
		doc = frappe.get_doc("Procurement Committee Evaluation", name)
		if doc.requested_by == BUYER or doc.owner == BUYER:
			if doc.docstatus == 1:
				doc.cancel()
			doc.delete(force=True)

	for doctype in ("Supplier Quotation", "Request for Quotation"):
		for name in frappe.get_all(doctype, filters={"docstatus": ["<", 2]}, pluck="name"):
			doc = frappe.get_doc(doctype, name)
			if any(row.item_code == ITEM for row in doc.items):
				if doc.docstatus == 1:
					doc.cancel()
				doc.delete(force=True)

	for supplier in SUPPLIERS:
		if frappe.db.exists("Supplier", supplier):
			frappe.delete_doc("Supplier", supplier, force=True, ignore_permissions=True)
	if frappe.db.exists("Item", ITEM):
		frappe.delete_doc("Item", ITEM, force=True, ignore_permissions=True)

	for user in (*MEMBERS, BUYER, OUTSIDER):
		# Notification Logs are not children of the evaluation, so deleting the document leaves
		# them behind -- and a stale one would make the notification checks below pass for the
		# wrong reason.
		for name in frappe.get_all("Notification Log", filters={"for_user": user}, pluck="name"):
			frappe.delete_doc("Notification Log", name, force=True, ignore_permissions=True)
		if frappe.db.exists("User", user):
			frappe.delete_doc("User", user, force=True, ignore_permissions=True)

	frappe.db.commit()


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


def make_supplier(name):
	return (
		frappe.get_doc({"doctype": "Supplier", "supplier_name": name, "supplier_group": "ICT"})
		.insert(ignore_permissions=True)
		.name
	)


def make_item():
	return (
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": ITEM,
				"item_name": ITEM,
				"item_group": "Services",
				"stock_uom": "Nos",
				"is_stock_item": 0,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def make_rfq(company):
	doc = frappe.get_doc(
		{
			"doctype": "Request for Quotation",
			"company": company,
			"transaction_date": nowdate(),
			"schedule_date": add_days(nowdate(), 7),
			"subject": "E2E competitive bidding",
			"message_for_supplier": "E2E: please quote.",
			# All three invited suppliers, including the one that quotes late: ERPNext refuses a
			# Supplier Quotation from a supplier the RFQ never invited.
			"suppliers": [{"supplier": supplier} for supplier in (*BIDS, *LATE_BID)],
			"items": [
				{
					"item_code": ITEM,
					"item_name": ITEM,
					"description": ITEM,
					"uom": "Nos",
					"conversion_factor": 1,
					"qty": 1,
					"schedule_date": add_days(nowdate(), 7),
				}
			],
		}
	)
	doc.insert(ignore_permissions=True)
	doc.submit()
	return doc


def make_quotation(rfq, supplier, rate, submit=True):
	doc = frappe.get_doc(
		{
			"doctype": "Supplier Quotation",
			"supplier": supplier,
			"company": rfq.company,
			"transaction_date": nowdate(),
			"valid_till": add_days(nowdate(), 30),
			"items": [
				{
					"item_code": ITEM,
					"item_name": ITEM,
					"description": ITEM,
					"uom": "Nos",
					"conversion_factor": 1,
					"qty": 1,
					"rate": rate,
					"schedule_date": add_days(nowdate(), 7),
					"request_for_quotation": rfq.name,
					"request_for_quotation_item": rfq.items[0].name,
				}
			],
		}
	)
	doc.insert(ignore_permissions=True)
	if submit:
		doc.submit()
	return doc


def make_evaluation(rfq):
	return frappe.get_doc(
		{
			"doctype": "Procurement Committee Evaluation",
			"request_for_quotation": rfq.name,
			"requested_by": BUYER,
			"members": [{"member": member} for member in MEMBERS],
		}
	).insert(ignore_permissions=True)


# --- checks ----------------------------------------------------------------------------------


def grid(doc):
	"""The grid as a {(member, quotation): row} map, which is how the sync keys it."""
	return {(row.member, row.supplier_quotation): row for row in doc.quotation_scores}


def await_notifications(evaluation, expected, timeout=30):
	"""Wait for the queue worker to write the Notification Logs, then return their recipients.

	`enqueue_create_notification` enqueues after commit outside tests, so the logs are written
	by the queue-short worker rather than inline -- which is the path production uses, and worth
	exercising rather than short-circuiting.
	"""
	deadline = time.monotonic() + timeout
	while True:
		frappe.db.commit()  # the worker writes in its own transaction; drop ours to see it
		found = frappe.get_all(
			"Notification Log",
			filters={"document_type": evaluation.doctype, "document_name": evaluation.name},
			fields=["for_user", "subject"],
		)
		if len(found) >= expected or time.monotonic() > deadline:
			return found
		time.sleep(1)


def run():
	print("\nProcurement Committee Evaluation — quotation scoring grid + committee notification\n")
	teardown()

	company = frappe.defaults.get_user_default("Company") or frappe.get_all("Company", pluck="name")[0]
	make_item()
	for supplier in SUPPLIERS:
		make_supplier(supplier)
	make_user(BUYER, "E2E Buyer", ["Purchase User"])
	for i, member in enumerate(MEMBERS, start=1):
		make_user(member, f"E2E Member {i}", ["Procurement Committee"])
	make_user(OUTSIDER, "E2E Outsider", ["Procurement Committee"])

	rfq = make_rfq(company)
	quotations = {supplier: make_quotation(rfq, supplier, rate) for supplier, rate in BIDS.items()}
	print(f"  RFQ {rfq.name} with {len(quotations)} quotations\n")

	# --- the grid is derived from the RFQ, not typed in ---------------------------------------
	evaluation = make_evaluation(rfq)
	rows = grid(evaluation)
	check(
		"one row per member per quotation",
		len(evaluation.quotation_scores) == len(MEMBERS) * len(BIDS),
		f"{len(evaluation.quotation_scores)} rows",
	)
	check(
		"every member/quotation pair present",
		set(rows) == {(m, q.name) for m in MEMBERS for q in quotations.values()},
	)
	check(
		"supplier and quoted amount copied from the quotation",
		all(
			rows[(member, quotation.name)].supplier == supplier
			and rows[(member, quotation.name)].quotation_amount == BIDS[supplier]
			for member in MEMBERS
			for supplier, quotation in quotations.items()
		),
	)
	check(
		"grid opens with the cheapest bid first",
		evaluation.quotation_scores[0].quotation_amount == min(BIDS.values()),
		f"{evaluation.quotation_scores[0].supplier} at {evaluation.quotation_scores[0].quotation_amount}",
	)

	# --- scores survive a rebuild -------------------------------------------------------------
	cheapest = min(BIDS, key=BIDS.get)
	scored_row = rows[(MEMBERS[0], quotations[cheapest].name)]
	scored_row.score = 88
	scored_row.comments = "Best value for money."
	evaluation.save()
	evaluation.reload()
	kept = grid(evaluation)[(MEMBERS[0], quotations[cheapest].name)]
	check("entered score survives a save", kept.score == 88 and kept.comments == "Best value for money.")
	row_name = kept.name

	# --- a late bid appears without the RFQ being re-picked -----------------------------------
	late_supplier, late_rate = next(iter(LATE_BID.items()))
	late = make_quotation(rfq, late_supplier, late_rate)
	evaluation.save()
	evaluation.reload()
	rows = grid(evaluation)
	check(
		"a quotation arriving later is picked up on the next save",
		len(evaluation.quotation_scores) == len(MEMBERS) * (len(BIDS) + 1)
		and (MEMBERS[0], late.name) in rows,
		f"{len(evaluation.quotation_scores)} rows",
	)
	check(
		"the rebuild reuses the scored row rather than replacing it",
		rows[(MEMBERS[0], quotations[cheapest].name)].name == row_name,
	)

	# --- rows are derived data: hand edits and stale rows do not survive ----------------------
	evaluation.append(
		"quotation_scores",
		{"member": OUTSIDER, "supplier_quotation": quotations[cheapest].name, "score": 100},
	)
	evaluation.save()
	evaluation.reload()
	check(
		"a row typed into the grid by hand is dropped",
		OUTSIDER not in {row.member for row in evaluation.quotation_scores},
	)

	evaluation.members = [row for row in evaluation.members if row.member != MEMBERS[1]]
	evaluation.save()
	evaluation.reload()
	check(
		"removing a member removes their rows",
		{row.member for row in evaluation.quotation_scores} == {MEMBERS[0]},
		f"{len(evaluation.quotation_scores)} rows left",
	)
	evaluation.append("members", {"member": MEMBERS[1]})
	evaluation.save()
	evaluation.reload()

	# --- a withdrawn bid leaves the competition, and does not freeze it -----------------------
	late.reload()
	late.cancel()
	evaluation.reload()
	check(
		"cancelling a quotation drops it out of the grid",
		late.name not in {row.supplier_quotation for row in evaluation.quotation_scores},
		f"{len(evaluation.quotation_scores)} rows left",
	)
	try:
		evaluation.save()
		check("the evaluation is still saveable after a bid is withdrawn", True)
	except Exception as e:  # noqa: BLE001
		check("the evaluation is still saveable after a bid is withdrawn", False, f"{type(e).__name__}: {e}")
	evaluation.reload()

	# --- the pre-existing controls still hold ------------------------------------------------
	def make_requester_a_member():
		doc = frappe.get_doc("Procurement Committee Evaluation", evaluation.name)
		doc.append("members", {"member": BUYER})
		doc.save()

	expect_throw("the requester still cannot sit on the committee", make_requester_a_member)

	# --- entering Committee Reviewing tells the named members, and nobody else ----------------
	evaluation.reload()
	apply_workflow(evaluation, "Send to Committee")
	evaluation.reload()
	check("workflow reaches Committee Reviewing", evaluation.workflow_state == COMMITTEE_REVIEW_STATE)

	notified = await_notifications(evaluation, expected=len(MEMBERS))
	recipients = {row.for_user for row in notified}
	check("both committee members got a bell notification", recipients == set(MEMBERS), str(sorted(recipients)))
	check(
		"the notification says what is wanted of them",
		all("score" in (row.subject or "").lower() for row in notified),
		notified[0].subject if notified else "",
	)
	check(
		"the Procurement Committee role at large was not told",
		OUTSIDER not in recipients,
	)

	emailed = frappe.get_all(
		"Email Queue Recipient",
		filters={
			"parent": (
				"in",
				frappe.get_all(
					"Email Queue",
					filters={"reference_doctype": evaluation.doctype, "reference_name": evaluation.name},
					pluck="name",
				)
				or [""],
			)
		},
		pluck="recipient",
	)
	check("both committee members were emailed too", set(MEMBERS) <= set(emailed), str(sorted(set(emailed))))

	# --- a member fills in their own row and nobody else's ------------------------------------
	# Everything above ran as Administrator, who is exempt from the self-scoring rule on purpose
	# (see enforce_self_scoring); the guardrail only means anything from inside a member's own
	# session, which is the only place a real score is ever entered.
	scored_quotation = quotations[cheapest].name
	try:
		frappe.set_user(MEMBERS[0])

		def reopen():
			return frappe.get_doc("Procurement Committee Evaluation", evaluation.name)

		def score_in_another_name():
			doc = reopen()
			grid(doc)[(MEMBERS[1], scored_quotation)].score = 95
			doc.save()

		expect_throw("a member cannot score in another member's name", score_in_another_name)

		def sign_off_in_another_name():
			doc = reopen()
			for row in doc.members:
				if row.member == MEMBERS[1]:
					row.reviewed = 1
			doc.save()

		expect_throw("a member cannot sign off another member's review", sign_off_in_another_name)

		def drop_a_colleague():
			doc = reopen()
			doc.members = [row for row in doc.members if row.member != MEMBERS[1]]
			doc.save()

		expect_throw("a member cannot drop a colleague off the committee", drop_a_colleague)

		doc = reopen()
		grid(doc)[(MEMBERS[0], scored_quotation)].score = 91
		grid(doc)[(MEMBERS[0], scored_quotation)].comments = "Cheapest, and prequalified."
		for row in doc.members:
			if row.member == MEMBERS[0]:
				row.reviewed = 1
		doc.save()
		saved = reopen()
		check(
			"a member can still score and sign their own row",
			grid(saved)[(MEMBERS[0], scored_quotation)].score == 91
			and any(row.reviewed for row in saved.members if row.member == MEMBERS[0]),
		)
		check(
			"the other member's row is untouched",
			not grid(saved)[(MEMBERS[1], scored_quotation)].score
			and not any(row.reviewed for row in saved.members if row.member == MEMBERS[1]),
		)
	finally:
		frappe.set_user("Administrator")

	# --- and the award still cannot be recommended without a quorum --------------------------
	expect_throw(
		"quorum still gates the move to Intent to Award",
		lambda: apply_workflow(
			frappe.get_doc("Procurement Committee Evaluation", evaluation.name),
			"Submit for Award Approval",
		),
	)

	print(f"\n  {len(PASS)} passed, {len(FAIL)} failed")
	if FAIL:
		print("  FAILED: " + "; ".join(FAIL))
	return {"passed": len(PASS), "failed": len(FAIL), "failures": FAIL}
