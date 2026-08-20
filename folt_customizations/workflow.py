import frappe
from frappe.model.workflow import get_workflow, get_workflow_name
from frappe.utils.user import get_users_with_role

# "Who is this waiting for?" is a question every FoLT approval chain raises and none of them
# answered on the document itself. The workflow state says *what* is pending ("Pending
# Approval") but not *who* can clear it, so anyone chasing an order had to open the Workflow
# to read the transitions and then cross-reference Role holders by hand.
#
# The answer is derived from the Workflow definition rather than from the Workflow Action
# rows frappe writes per transition. Both agree -- frappe fills a Workflow Action's
# `permitted_roles` from these same transitions -- but the definition is the thing that is
# true by construction. Workflow Action rows are notification plumbing: they are created by a
# doc_event, cleaned up when a transition completes, and absent entirely on documents that
# moved before that machinery existed. notifications.py reads them because it is reacting to
# one being inserted; a form that just wants to display the current answer should not depend
# on them existing.


@frappe.whitelist()
def get_pending_approvers(doctype: str, name: str) -> dict:
    """Return the roles that can move a document out of its current state, and who holds them.

    Empty dict when nothing is pending -- no workflow on the doctype, or a final state with no
    transitions leading out of it (Approved / Rejected). Callers treat that as "show nothing".
    """
    frappe.has_permission(doctype, doc=name, throw=True)

    workflow_name = get_workflow_name(doctype)
    if not workflow_name:
        return {}

    workflow = get_workflow(doctype)
    state = frappe.db.get_value(doctype, name, workflow.workflow_state_field)
    if not state:
        return {}

    return get_approvers_for_state(workflow, state)


def get_approvers_for_state(workflow, state: str) -> dict:
    """The roles allowed to act on `state`, resolved to the users holding them.

    Split out from get_pending_approvers so the resolution can be exercised for any state
    without having to drive a real document into it first.
    """
    roles = sorted({t.allowed for t in workflow.transitions if t.state == state and t.allowed})
    if not roles:
        return {}

    # get_users_with_role() already drops disabled accounts and Administrator, so what comes
    # back is people who could actually sign in and act. A user holding two of the roles is
    # listed once, under the first -- naming the same person twice reads like two approvals
    # are needed when only one is.
    seen: set[str] = set()
    by_user: dict[str, str] = {}
    for role in roles:
        for user in get_users_with_role(role):
            if user not in seen:
                seen.add(user)
                by_user[user] = role

    if not seen:
        # A role nobody holds is worth surfacing rather than hiding: it means the document is
        # stuck, and the fix is a Role assignment, not another reminder to a person.
        return {"state": state, "roles": roles, "approvers": [], "unassigned": True}

    names = frappe.get_all(
        "User", filters={"name": ("in", list(seen))}, fields=["name", "full_name"]
    )
    approvers = sorted(
        (
            {"user": u.name, "full_name": u.full_name or u.name, "role": by_user[u.name]}
            for u in names
        ),
        key=lambda a: a["full_name"].casefold(),
    )
    return {"state": state, "roles": roles, "approvers": approvers, "unassigned": False}
