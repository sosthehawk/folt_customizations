"""End-to-end check that approvals name their approver and carry their signature.

Walks an Activity Requisition through its chain, each step taken by the person who takes it in
real life, and proves:

  stamped      every move writes a FoLT Approval Signature row: who, their name, the role they
               acted in, when, and the action
  signed       a signature drawn in the Desk dialog (hold_signature) lands on the step it was
               drawn for and is consumed by it; failing that, the user's saved signature is used;
               failing that, the step is still named, unsigned
  returned     a return for revision supersedes the pass before it, and records its reason
  printed      folt_approvals / folt_signoff give the prints the standing steps, and the standard
               print renders the approvers and their signatures through the hidden field
  derived      signing_map asks a signature for approval moves only -- never a turn-down, never
               post-approval bookkeeping
  removed      deleting the document deletes its steps

Reuses activity_planning_e2e's users, requisition and teardown. Idempotent. Run with:

    bench --site <site> execute folt_customizations.approval_signatures_e2e.run
"""

import frappe

from folt_customizations import activity_planning_e2e as ape
from folt_customizations import approval_signatures as sig

ACTIVITY = f"{ape.MARK} Signed Approvals Forum"

# A 1x1 PNG: a real image data URI, the shape frappe's signature pad produces.
PNG_A = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
PNG_B = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="

check, expect_throw, as_user = ape.check, ape.expect_throw, ape.as_user
DT = "Activity Requisition"


def steps(name, current_only=False):
	return sig.approvals_for(DT, name, current_only=current_only)


def run():
	frappe.set_user("Administrator")
	frappe.flags.mute_emails = True
	ape.require_users()
	ape.teardown()
	saved = {u: frappe.db.get_value("User", u, sig.USER_SIGNATURE_FIELD) for u in ape.USERS}
	frappe.db.commit()
	try:
		walk()
	finally:
		frappe.db.rollback()
		frappe.set_user("Administrator")
		ape.teardown()
		for user, value in saved.items():
			frappe.db.set_value("User", user, sig.USER_SIGNATURE_FIELD, value, update_modified=False)
		frappe.db.commit()

	print(f"\n{'=' * 60}\nRESULT: {len(ape.PASS)} passed, {len(ape.FAIL)} failed")
	if ape.FAIL:
		for label in ape.FAIL:
			print(f"  FAILED: {label}")
		raise SystemExit(1)


def walk():
	print("\n--- which moves ask for a signature ---")
	smap = sig.signing_map()
	ar = {tuple(p) for p in smap.get(DT, {}).get("signs", [])}
	check("the requester's submission is signed", ("Draft", "Submit for Review") in ar)
	check("each head's approval is signed", {("Pending Head of Programs", "Approve"), ("Pending Head of Finance", "Approve")} <= ar)
	check("a return for revision is not signed (it asks for a reason)", ("Pending Head of Programs", "Return for Revision") not in ar)
	ea = {tuple(p) for p in smap.get("Employee Advance", {}).get("signs", [])}
	check("post-approval bookkeeping is not signed", ("Approved", "Record Disbursement") not in ea and ("Checked", "Approve") in ea, str(sorted(ea)))
	check("master-data lifecycles are not signed", "FoLT Donor" not in smap)

	print("\n--- the user's own signature ---")
	for user in ape.USERS:
		frappe.db.set_value("User", user, sig.USER_SIGNATURE_FIELD, None, update_modified=False)
	as_user(ape.REQUESTER, lambda: sig.save_my_signature(PNG_A))
	check("anyone keeps their own signature without write on User", frappe.db.get_value("User", ape.REQUESTER, sig.USER_SIGNATURE_FIELD) == PNG_A)
	expect_throw("something that is not a drawn signature is refused", lambda: as_user(ape.REQUESTER, lambda: sig.save_my_signature("javascript:alert(1)")))

	print("\n--- stamped and signed ---")
	name = ape.raise_requisition(ACTIVITY, attach=True)
	check("a new document has no steps", steps(name) == [])

	ape.act(ape.REQUESTER, name, "Submit for Review")
	rows = steps(name)
	first = rows[0] if rows else frappe._dict()
	check("submitting stamps one step", len(rows) == 1, str(len(rows)))
	check("it names the requester and the role they acted in", first.approver == ape.REQUESTER and first.role == "Employee" and first.approver_name == frappe.utils.get_fullname(ape.REQUESTER), f"{first.approver} {first.role}")
	check("with no pad, the saved signature is used", first.signature == PNG_A)
	check("the step records its action and states", (first.action, first.from_state, first.to_state) == ("Submit for Review", "Draft", "Pending Head of Programs"))

	def hop_signs_and_approves():
		sig.hold_signature(DT, name, PNG_B)
		return frappe.get_doc(DT, name)

	doc = as_user(ape.HEAD_OF_PROGRAMS, hop_signs_and_approves)
	held_key = (DT, name, frappe.db.get_value(DT, name, "modified"))
	ape.act(ape.HEAD_OF_PROGRAMS, name, "Approve")
	rows = steps(name)
	hop = rows[-1]
	check("the signature drawn for the step is the one stamped", hop.signature == PNG_B and hop.role == "Head of Programs", f"{hop.role}")
	check("and it is consumed by that step", as_user(ape.HEAD_OF_PROGRAMS, lambda: sig.held_signature(*held_key)) is None)
	check("hold_signature without 'remember' leaves the saved one alone", not frappe.db.get_value("User", ape.HEAD_OF_PROGRAMS, sig.USER_SIGNATURE_FIELD))
	expect_throw("an empty signature cannot be held", lambda: as_user(ape.HEAD_OF_FINANCE, lambda: sig.hold_signature(DT, name, "")))
	del doc

	print("\n--- returned: the pass before it no longer stands ---")
	ape.act(ape.HEAD_OF_FINANCE, name, "Return for Revision", reason="E2E: the venue quote is missing.")
	rows = steps(name)
	back = rows[-1]
	check("the return is recorded, with its reason and no signature", back.turn_down and back.reason == "E2E: the venue quote is missing." and not back.signature)
	check("every earlier step is superseded", all(r.superseded for r in rows), str([(r.action, r.superseded) for r in rows]))
	check("so nothing stands for the print", steps(name, current_only=True) == [])

	print("\n--- the second pass ---")
	ape.act(ape.REQUESTER, name, "Submit for Review")
	ape.act(ape.HEAD_OF_PROGRAMS, name, "Approve")
	unsigned = steps(name)[-1]
	check("an approval from a screen with no pad, by someone with no saved signature, is still named", unsigned.approver == ape.HEAD_OF_PROGRAMS and not unsigned.signature)

	def hof_signs_remembering_and_approves():
		sig.hold_signature(DT, name, PNG_B, remember=1)

	as_user(ape.HEAD_OF_FINANCE, hof_signs_remembering_and_approves)
	ape.act(ape.HEAD_OF_FINANCE, name, "Approve")
	check("'remember' keeps the drawn signature as the user's own", frappe.db.get_value("User", ape.HEAD_OF_FINANCE, sig.USER_SIGNATURE_FIELD) == PNG_B)
	check("the requisition is approved", frappe.db.get_value(DT, name, "workflow_state") == "Approved")

	print("\n--- printed ---")
	doc = frappe.get_doc(DT, name)
	standing = sig.folt_approvals(doc)
	check("three steps stand on the approved requisition", [s.action for s in standing] == ["Submit for Review", "Approve", "Approve"], str([s.action for s in standing]))
	signer = sig.folt_signoff(doc, ["Approve"])
	check("folt_signoff picks the latest approver for a cell", signer and signer.approver == ape.HEAD_OF_FINANCE)
	check("folt_signoff is empty for an action not taken", sig.folt_signoff(doc, "Authorise") is None)

	frappe.utils.set_request(method="GET", path="/printview")
	html = frappe.get_print(DT, name, print_format="Standard")
	check("the standard print names every standing approver", all(frappe.utils.get_fullname(u) in html for u in ape.USERS))
	check("and carries their signatures", PNG_B in html and PNG_A in html)
	check("but not the superseded pass's return reason", "venue quote is missing" not in html.split("folt-approvals")[-1])

	result = as_user(ape.HEAD_OF_FINANCE, lambda: sig.get_approvals(DT, name))
	check("get_approvals gives the whole history to a reader", len(result["steps"]) == 6 and result["signing"], str(len(result["steps"])))
	payload = as_user(ape.HEAD_OF_FINANCE, lambda: sig.document_payload(DT, name))
	check("document screens get the steps and the reader's own signature", len(payload["approvals"]) == 6 and payload["my_signature"] == PNG_B)

	print("\n--- older documents: names rebuilt from the timeline ---")
	before = [(r.action, r.approver, r.to_state, r.turn_down, r.superseded) for r in steps(name)]
	frappe.db.delete(sig.DOCTYPE, {"reference_doctype": DT, "reference_name": name})
	check("a document moved before signatures has no steps", steps(name) == [])
	result = sig.backfill([DT])
	rebuilt = steps(name)
	check("backfill rebuilds its steps from the Workflow comments", result.get(DT, 0) >= 1 and len(rebuilt) == len(before), f"{len(rebuilt)} of {len(before)}")
	check("with the same people, actions, states and superseding", [(r.action, r.approver, r.to_state, r.turn_down, r.superseded) for r in rebuilt] == before,
		str([(r.action, r.approver, r.superseded) for r in rebuilt]))
	check("the return keeps its reason", any(r.turn_down and r.reason == "E2E: the venue quote is missing." for r in rebuilt))
	check("rebuilt steps are marked as history and carry no signature", all(r.backfilled and not r.signature for r in rebuilt))
	check("backfill is idempotent", sig.backfill([DT]).get(DT, 0) == 0 and len(steps(name)) == len(before))

	print("\n--- removed with the document ---")
	frappe.db.rollback()  # nothing above is kept; reproduce one row and delete its document
	name2 = ape.raise_requisition(f"{ACTIVITY} Two", attach=True)
	ape.act(ape.REQUESTER, name2, "Submit for Review")
	check("a step exists before the delete", len(steps(name2)) == 1)
	frappe.delete_doc(DT, name2, force=True, ignore_permissions=True)
	check("deleting the document deletes its steps", steps(name2) == [])
