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

ADMINISTRATOR = "Administrator"


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


def is_own_todo(owner: str, roles: list[str]) -> bool:
    """Whether the action pending on a document belongs to the person who raised it.

    True when the author themselves holds a role that can make the next move -- so the document
    is not waiting on an approver at all, it is waiting on its own author to finish with it.

    NOT "the document is in its first state", and the difference is the whole point. Employee
    Advance Float Approval is created in `Requested` and the action out of `Requested` belongs to
    the Finance Officer, so a rule keyed on the initial state would hide the one item there that
    genuinely is somebody's task. An Activity Requisition in `Draft` moves on by `Employee`, which
    the requester holds, so that one is theirs.

    Stated once because two places need it and they get it wrong in opposite directions:

      notifications.notify_pending_approvers stays quiet. Before this guard, one new Purchase
      Order alerted every Purchase User on the site while it was still a draft.

      folt_tasks.my_tasks keeps it out of *everybody's* approval queue -- the author's included,
      but everybody else's too. Somebody else's unfinished draft is not a task: it is going to be
      submitted by the person writing it, and a queue full of other people's drafts is a queue
      nobody reads. It still shows in its author's Drafts, which is where unfinished work belongs.
    """
    return not set(roles).isdisjoint(frappe.get_roles(owner))


def get_approvers_for_state(workflow, state: str) -> dict:
    """The roles allowed to act on `state`, resolved to the users holding them.

    Split out from get_pending_approvers so the resolution can be exercised for any state
    without having to drive a real document into it first.
    """
    roles = sorted({t.allowed for t in workflow.transitions if t.state == state and t.allowed})
    if not roles:
        return {}

    # get_users_with_role() already drops disabled accounts, so what comes back is people who
    # could actually sign in and act. A user holding two of the roles is listed once, under the
    # first -- naming the same person twice reads like two approvals are needed when only one is.
    seen: set[str] = set()
    by_user: dict[str, str] = {}
    for role in roles:
        for user in get_users_with_role(role):
            if user not in seen:
                seen.add(user)
                by_user[user] = role

    # `unassigned` is decided HERE, on the real role holders, and deliberately before
    # Administrator is added below. A role nobody holds is worth surfacing rather than hiding: it
    # means the document is stuck, and the fix is a Role assignment, not another reminder to a
    # person. Administrator holds every role, so deciding this after adding it would mean no role
    # is ever unassigned again and the warning could never fire.
    unassigned = not seen

    administrator = _administrator_as_approver(roles)
    if administrator:
        seen.add(administrator["user"])
        by_user[administrator["user"]] = administrator["role"]

    if not seen:
        return {"state": state, "roles": roles, "approvers": [], "unassigned": unassigned}

    names = frappe.get_all(
        "User", filters={"name": ("in", list(seen))}, fields=["name", "full_name"]
    )
    approvers = sorted(
        (
            {"user": u.name, "full_name": u.full_name or u.name, "role": by_user[u.name]}
            for u in names
        ),
        # Administrator last, whatever it is called. The list is read as "who to chase", and the
        # account that holds every role by definition is the fallback rather than the intended
        # approver -- sorting it by name would put "Administrator" ahead of every real person.
        key=lambda a: (a["user"] == ADMINISTRATOR, a["full_name"].casefold()),
    )
    return {"state": state, "roles": roles, "approvers": approvers, "unassigned": unassigned}


def _administrator_as_approver(roles: list[str]) -> dict | None:
    """Administrator, as a holder of `roles`, or None if the account cannot act.

    Frappe's get_users_with_role excludes Administrator by name (utils/user.py), not by looking
    at Has Role -- so no role assignment can ever bring it back and this is the only place it can
    be added. It belongs in the answer: frappe.get_roles("Administrator") returns every role on
    the site, which is what workflow_access.enforce_state_custodian and the guide's `can_act`
    both consult, so leaving it out meant a document Administrator could plainly act on named
    nobody who could act on it.

    DISPLAY ONLY. This is not wired into notifications.notify_pending_approvers or
    float_lifecycle._notify_overdue on purpose: holding every role means Administrator would be
    alerted on every transition of all nine workflows, and the account's address here is the
    installer's placeholder rather than a mailbox anyone reads.
    """
    if not frappe.db.get_value("User", ADMINISTRATOR, "enabled"):
        return None

    # Attributed to the first role asked for. Administrator holds all of them, so any one of them
    # is as true as the next, and `roles` is already sorted.
    return {"user": ADMINISTRATOR, "role": roles[0]}
