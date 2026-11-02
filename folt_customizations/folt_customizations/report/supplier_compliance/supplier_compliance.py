"""Each active supplier's file: KRA PIN, pre-qualification and when it lapses, hold or disabled,
a contact email, bank details on record (whether, never the number), and the waivers raised to
buy from it without competition."""

import frappe
from frappe import _
from frappe.utils import getdate, nowdate


def execute(filters=None):
	f = frappe._dict(filters or {})
	where = []
	if f.supplier_group:
		where.append(["supplier_group", "=", f.supplier_group])
	suppliers = frappe.get_list("Supplier", filters=where, fields=["name", "supplier_name", "supplier_group", "tax_id", "folt_qualified_until", "on_hold", "disabled", "email_id"],
	                            order_by="supplier_name", limit_page_length=0)
	names = [s.name for s in suppliers] or [""]
	banks = {}
	for b in frappe.get_all("Bank Account", filters={"party_type": "Supplier", "party": ["in", names]}, fields=["party"]):
		banks[b.party] = banks.get(b.party, 0) + 1
	waivers = {}
	for w in frappe.get_all("Derogation Waiver Request", filters={"supplier": ["in", names], "docstatus": ["<", 2]}, fields=["supplier"]):
		waivers[w.supplier] = waivers.get(w.supplier, 0) + 1
	today = getdate(nowdate())
	rows = []
	for s in suppliers:
		qualified = None if not s.folt_qualified_until else getdate(s.folt_qualified_until) >= today
		gaps = [g for g, bad in ((_("no KRA PIN"), not s.tax_id), (_("qualification lapsed"), qualified is False), (_("no contact email"), not s.email_id),
		                          (_("no bank details"), not banks.get(s.name))) if bad]
		rows.append({"supplier": s.name, "supplier_name": s.supplier_name, "supplier_group": s.supplier_group, "kra_pin": s.tax_id,
		             "qualified_until": s.folt_qualified_until, "qualified": _("Yes") if qualified else (_("Lapsed") if qualified is False else _("Not pre-qualified")),
		             "status": _("Blocked") if s.on_hold else (_("Inactive") if s.disabled else _("Active")), "email": s.email_id,
		             "bank_accounts": banks.get(s.name, 0), "waivers": waivers.get(s.name, 0), "gaps": ", ".join(gaps) or _("None")})
	columns = [
		{"fieldname": "supplier", "label": _("Supplier"), "fieldtype": "Link", "options": "Supplier", "width": 180},
		{"fieldname": "supplier_group", "label": _("Group"), "fieldtype": "Link", "options": "Supplier Group", "width": 130},
		{"fieldname": "kra_pin", "label": _("KRA PIN"), "fieldtype": "Data", "width": 120},
		{"fieldname": "qualified", "label": _("Pre-qualified"), "fieldtype": "Data", "width": 120},
		{"fieldname": "qualified_until", "label": _("Until"), "fieldtype": "Date", "width": 100},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 90},
		{"fieldname": "email", "label": _("Email"), "fieldtype": "Data", "width": 180},
		{"fieldname": "bank_accounts", "label": _("Bank accounts"), "fieldtype": "Int", "width": 100},
		{"fieldname": "waivers", "label": _("Waivers"), "fieldtype": "Int", "width": 80},
		{"fieldname": "gaps", "label": _("Gaps"), "fieldtype": "Data", "width": 220},
	]
	return columns, rows
