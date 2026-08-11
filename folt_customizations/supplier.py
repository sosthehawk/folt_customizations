import frappe
from frappe import _

# A FoLT supplier can be pre-qualified for more than one category -- a travel firm that
# also hires out vehicles sits in both "Travel & Accommodation" and "Car Hire". ERPNext's
# Supplier has a single `supplier_group` Link, so the extra categories live in the
# `folt_additional_supplier_groups` Table MultiSelect Custom Field (shipped as a fixture),
# which reuses ERPNext's stock `Supplier Group Item` child doctype.
#
# `supplier_group` stays the PRIMARY group and is what every standard ERPNext report,
# Pricing Rule and Tax Rule reads -- the additional groups are FoLT's pre-qualification
# register only. Use `get_supplier_groups()` / `suppliers_in_group()` below rather than
# reading `supplier_group` directly when you need "is this supplier qualified for X".

ADDITIONAL_GROUPS_FIELD = "folt_additional_supplier_groups"


def validate(doc, method=None):
    """Keep the additional supplier groups coherent with the primary one.

    Runs on Supplier.validate via doc_events. Three rules, in order:
      1. drop blank rows (the grid leaves one behind when a user clears a cell);
      2. drop any row that repeats the primary `supplier_group` -- it is implied, and
         leaving it in double-counts the supplier in any group rollup;
      3. reject group (non-leaf) nodes, matching how a supplier is never filed under a
         parent node like "All Supplier Groups".
    Duplicates inside the table are already blocked by the Table MultiSelect widget, but
    rule 2 has to be done here because the widget cannot see the primary field.
    """
    rows = doc.get(ADDITIONAL_GROUPS_FIELD) or []
    kept, seen = [], set()

    for row in rows:
        group = (row.supplier_group or "").strip()
        if not group or group in seen:
            continue
        if group == doc.supplier_group:
            continue
        if frappe.db.get_value("Supplier Group", group, "is_group"):
            frappe.throw(
                _("{0} is a group node and cannot be used as an additional supplier group.").format(
                    frappe.bold(group)
                ),
                title=_("Invalid Supplier Group"),
            )
        seen.add(group)
        kept.append(row)

    if len(kept) != len(rows):
        doc.set(ADDITIONAL_GROUPS_FIELD, kept)


def get_supplier_groups(supplier: str) -> list[str]:
    """Every group a supplier is qualified for: the primary one first, then the extras."""
    primary = frappe.db.get_value("Supplier", supplier, "supplier_group")
    extra = frappe.get_all(
        "Supplier Group Item",
        filters={"parent": supplier, "parenttype": "Supplier", "parentfield": ADDITIONAL_GROUPS_FIELD},
        pluck="supplier_group",
        order_by="idx",
    )
    return ([primary] if primary else []) + [g for g in extra if g != primary]


def suppliers_in_group(supplier_group: str) -> list[str]:
    """Suppliers qualified for a group, whether it is their primary or an additional one.

    The standard Supplier list/report filter only matches the primary field, so anything
    that needs the full pre-qualified register for a category must go through here.
    """
    primary = frappe.get_all(
        "Supplier", filters={"supplier_group": supplier_group}, pluck="name"
    )
    additional = frappe.get_all(
        "Supplier Group Item",
        filters={
            "supplier_group": supplier_group,
            "parenttype": "Supplier",
            "parentfield": ADDITIONAL_GROUPS_FIELD,
        },
        pluck="parent",
    )
    return sorted(set(primary) | set(additional))
