"""End-to-end check that the root of the Department tree is editable, and only the root.

The point of department.py is a negative one -- a save that used to raise no longer does --
and a negative is exactly the kind of fix that quietly stops working. Both of the blockers it
clears live in erpnext, not here: `company` being `reqd`, and `Department.validate` handing
the root itself as its own parent. An erpnext upgrade can change either, and the failure mode
is asymmetric. If erpnext fixes them, this app is harmlessly redundant. If erpnext *moves*
them -- a different mandatory field on the root, a different self-parenting path -- the root
goes back to being unsaveable and nobody notices until an HR administrator tries to edit the
inherited leave approvers and gets a stack trace.

Four things are worth naming:

  - THE ROOT'S lft/rgt SURVIVE A SAVE. This is the whole basis for putting `parent_department`
    back to blank rather than to something valid: blank matches what the column already holds,
    so `update_nsm` sees no move and does no tree surgery. If a save ever starts shifting the
    root's bounds, the nested set is being rebuilt underneath the entire department tree and
    every ancestor walk -- get_approvers included -- is suspect.

  - THE COMPANY REQUIREMENT IS STILL REAL FOR EVERY OTHER NODE. `mandatory_depends_on` is
    evaluated by the browser only; the server ignores it entirely. That is asserted here by
    blanking a child's company over the API, which the Property Setter alone would let
    through. This test is the only thing standing between "the root is editable" and "any
    department can be saved with no company".

  - THE ASTERISK IS STILL THERE. Turning `reqd` off without `mandatory_depends_on` would drop
    the client-side prompt too, and a required field that only complains after you press Save
    is a worse form. Both Property Setters have to survive together.

  - AN APPROVER ON THE ROOT REACHES A REAL EMPLOYEE. The tree fix is a means; the end is that
    `get_approvers` returns the root's approver for an employee several levels down. That
    round trip is checked rather than assumed.

Mutates inside a transaction and rolls back -- no fixtures, nothing to tear down. Run with

    bench --site <site> execute folt_customizations.department_e2e.run
"""

import frappe
from frappe.utils.nestedset import get_root_of

PASS, FAIL = [], []

HOOK = "folt_customizations.department.validate"


def check(label, condition, detail=""):
    (PASS if condition else FAIL).append(label)
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")


def _a_child_department() -> str | None:
    """Any non-root, non-group department -- the shape every real department has."""
    root = get_root_of("Department")
    for name in frappe.get_all("Department", pluck="name", order_by="lft"):
        if name != root:
            return name
    return None


def run():
    root = get_root_of("Department")
    child = _a_child_department()

    print("\n--- the two Property Setters, which have to travel together ---")

    frappe.clear_cache(doctype="Department")
    company = frappe.get_meta("Department").get_field("company")
    check("Department.company is no longer unconditionally reqd", not company.reqd)
    check(
        "but the client still demands it for any node with a parent",
        company.mandatory_depends_on == "eval:doc.parent_department",
        f"got {company.mandatory_depends_on!r}",
    )

    print("\n--- the hook is wired ---")

    hooked = frappe.get_hooks("doc_events").get("Department", {}).get("validate") or []
    if isinstance(hooked, str):
        hooked = [hooked]
    check("Department.validate runs folt_customizations.department.validate", HOOK in hooked)

    print("\n--- the root saves, and comes through a save unchanged ---")

    if not root:
        check("the Department tree has a root", False)
        return _result()

    before = frappe.db.get_value(
        "Department", root, ["company", "parent_department", "lft", "rgt"], as_dict=True
    )
    doc = frappe.get_doc("Department", root)
    try:
        doc.save()
        saved = True
    except Exception as e:
        saved = False
        check(f"{root} saves", False, f"{type(e).__name__}: {str(e)[:160]}")

    if saved:
        check(f"{root} saves", True)
        after = frappe.db.get_value(
            "Department", root, ["company", "parent_department", "lft", "rgt"], as_dict=True
        )
        check("the root is still company-less", not after.company, f"got {after.company!r}")
        check(
            "the root is still parentless, not its own parent",
            not after.parent_department,
            f"got {after.parent_department!r}",
        )
        check(
            "the nested set was not disturbed",
            (after.lft, after.rgt) == (before.lft, before.rgt),
            f"{before.lft}/{before.rgt} -> {after.lft}/{after.rgt}",
        )

    print("\n--- and the point of all of it: an approver on the root, edited normally ---")

    employee = frappe.db.get_value("Employee", {"status": "Active"}, "name")
    approver = frappe.db.get_value("User", {"enabled": 1, "user_type": "System User"}, "name")

    if saved and approver:
        doc = frappe.get_doc("Department", root)
        already = [r.approver for r in (doc.get("leave_approvers") or [])]
        if approver not in already:
            doc.append("leave_approvers", {"approver": approver})
        try:
            doc.save()
            check("a leave approver can be added to the root through a normal save", True)
        except Exception as e:
            check(
                "a leave approver can be added to the root through a normal save",
                False,
                f"{type(e).__name__}: {str(e)[:160]}",
            )

        if employee:
            from hrms.hr.doctype.department_approver.department_approver import get_approvers

            try:
                found = {r[0] for r in get_approvers(
                    "User", "", "name", 0, 20, {"employee": employee, "doctype": "Leave Application"}
                )}
                check(
                    "and it reaches an employee further down the tree",
                    approver in found,
                    f"{employee} sees {sorted(found)}",
                )
            except Exception as e:
                check(
                    "and it reaches an employee further down the tree",
                    False,
                    f"{type(e).__name__}: {str(e)[:160]}",
                )
    else:
        check("there is an enabled System User to name as approver", bool(approver))

    print("\n--- every other node still needs a company, server-side ---")

    if child:
        doc = frappe.get_doc("Department", child)
        doc.company = None
        try:
            doc.save()
            check(
                "a child department with no company is rejected",
                False,
                "it SAVED -- mandatory_depends_on is client-side only, so department.validate "
                "is the only thing enforcing this",
            )
        except frappe.ValidationError:
            check("a child department with no company is rejected", True)
        except Exception as e:
            check(
                "a child department with no company is rejected",
                False,
                f"raised {type(e).__name__} rather than ValidationError: {str(e)[:120]}",
            )

        doc = frappe.get_doc("Department", child)
        try:
            doc.save()
            check("and saves normally with its company intact", True)
        except Exception as e:
            check(
                "and saves normally with its company intact",
                False,
                f"{type(e).__name__}: {str(e)[:160]}",
            )
    else:
        check("there is a non-root department to test against", False)

    return _result()


def _result():
    # Everything above was written inside the transaction bench opened; none of it is wanted.
    frappe.db.rollback()

    print("\n" + "=" * 60)
    print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for label in FAIL:
            print(f"  FAILED: {label}")

    return {"passed": len(PASS), "failed": len(FAIL), "failures": FAIL}
