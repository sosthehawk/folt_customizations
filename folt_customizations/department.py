"""Make the root of the Department tree editable, without loosening any other node.

`All Departments` is the one Department nobody can save. It has two independent blockers,
and clearing either one alone still leaves the other:

  1. `company` is `reqd: 1` on erpnext's DocType and the root's is NULL -- which is how
     erpnext ships the tree root, since the root spans every company and belongs to none.
     A save dies on `MandatoryError: [Department, All Departments]: company`.

  2. `Department.validate` fills a blank `parent_department` with `get_root_of("Department")`.
     On the root that resolves to the root itself, so the save then dies one step later on
     `NestedSetRecursionError: Item cannot be added to its own descendants`.

This is worth fixing because `Department.leave_approvers` is one of only two sources feeding
the Leave Approver field on a Leave Application -- the other being `Employee.leave_approver` --
and `department_approver.get_approvers` walks *up* the tree. An approver on the root is
therefore inherited by every department, present and future, which makes it the single row an
HR administrator most wants to keep current, and the one row the Desk refuses to save. Until
this existed, approvers had to be written straight into the `Department Approver` child table
by script.

Blocker 1 is cleared by the two Property Setters in fixtures/property_setter.json: `reqd` off,
and `mandatory_depends_on = eval:doc.parent_department`, which restores the red asterisk and
the client's Missing Fields dialog for every node that has a parent -- that is, every node
except the root. `mandatory_depends_on` is evaluated by the BROWSER ONLY: with it in place, a
child department saved through the API with `company` blanked goes straight through (checked
in department_e2e). So `validate` below restates the requirement server-side. Without that
restatement, turning `reqd` off would quietly admit company-less departments through every
non-Desk path -- the API, a patch, a data import.

Blocker 2 is cleared by `validate` below putting `parent_department` back to blank on the
root. doc_events compose *after* the controller method, so this runs once erpnext's own
validate has done the damage and undoes exactly that one assignment. Blank is what the column
already holds, and `old_parent` is NULL as well, so `update_nsm` sees no change of parent and
does no tree work -- the root's `lft`/`rgt` come through a save untouched.
"""

import frappe
from frappe import _
from frappe.utils.nestedset import get_root_of


def validate(doc, method=None):
    """Runs on Department.validate via doc_events, after erpnext's own validate.

    Two mutually exclusive jobs, because the root and everything below it want opposite
    things: the root needs the parent erpnext just gave it taken away again, and every other
    node needs the mandatory company that the Property Setter took away.
    """
    if is_root(doc):
        doc.parent_department = ""
        return

    if not doc.company:
        frappe.throw(
            _("Company is required for {0}.").format(
                frappe.bold(doc.department_name or doc.name)
            ),
            title=_("Missing Company"),
        )


def is_root(doc) -> bool:
    """The tree root -- the only Department entitled to no company and no parent.

    `get_root_of` is erpnext's own answer to the same question, reused so that the two agree
    by construction. A document still being inserted is never the root: it is named before
    validate runs, but a new node always has a parent and a company, and treating it as the
    root would exempt it from the company check.
    """
    return not doc.is_new() and doc.name == get_root_of("Department")
