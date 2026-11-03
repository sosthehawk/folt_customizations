"""Fixtures the procurement e2e scripts share, now that procurement follows FoLT's workflow.

An RFQ is sent only from an approved purchase requisition (sourcing.before_submit_rfq), and a
committee must seat a Program Department member (committee.check_composition). Each e2e that
builds a competition needs both, made the same way, so they are made here.
"""

import frappe
from frappe.model.workflow import apply_workflow
from frappe.utils import add_days, nowdate

from folt_customizations.committee import program_department

JUSTIFICATION = "E2E: approved budget and activity concept note from the Program Department."


def approved_requisition(company, item, qty=1, rate=1000, schedule_date=None, title="E2E requisition"):
	"""A purchase requisition taken through FoLT Purchase Requisition Approval (HoP, then HoF).

	Run as Administrator, who holds every role and is exempt from the self-approval rule -- the
	e2e checks that test who may approve do so on their own documents.
	"""
	schedule_date = schedule_date or add_days(nowdate(), 14)
	doc = frappe.get_doc(
		{
			"doctype": "Material Request",
			"material_request_type": "Purchase",
			"company": company,
			"title": title,
			"transaction_date": nowdate(),
			"schedule_date": schedule_date,
			"folt_justification": JUSTIFICATION,
			"items": [
				{
					"item_code": item,
					"qty": qty,
					"uom": frappe.db.get_value("Item", item, "stock_uom") or "Nos",
					"conversion_factor": 1,
					"schedule_date": schedule_date,
					"folt_estimated_rate": rate,
				}
			],
		}
	).insert(ignore_permissions=True)
	for action in ("Submit for Review", "Approve", "Approve"):
		doc = apply_workflow(doc, action)
	return frappe.get_doc("Material Request", doc.name)


def rfq_lines(requisition, schedule_date=None):
	"""RFQ item rows that point back at the requisition, as ERPNext's own mapper writes them."""
	return [
		{
			"item_code": row.item_code,
			"item_name": row.item_name,
			"description": row.description or row.item_name,
			"uom": row.uom,
			"conversion_factor": row.conversion_factor or 1,
			"qty": row.qty,
			"schedule_date": schedule_date or row.schedule_date,
			"material_request": requisition.name,
			"material_request_item": row.name,
		}
		for row in requisition.items
	]


def ensure_program_member(user, company=None):
	"""Make `user` a Program Department representative: an active Employee in that department."""
	department = program_department()
	if not department:
		frappe.throw("No Programs Department is configured on FoLT Procurement Settings.")
	name = frappe.db.get_value("Employee", {"user_id": user}, "name")
	if name:
		frappe.db.set_value("Employee", name, {"department": department, "status": "Active"})
		return name
	first = frappe.db.get_value("User", user, "first_name") or user.split("@")[0]
	return (
		frappe.get_doc(
			{
				"doctype": "Employee",
				"first_name": first,
				"last_name": "E2E",
				"gender": "Prefer not to say",
				"date_of_birth": "1990-01-01",
				"date_of_joining": "2026-01-01",
				"company": company or frappe.defaults.get_user_default("Company") or frappe.get_all("Company", pluck="name")[0],
				"status": "Active",
				"user_id": user,
				"department": department,
				"create_user_permission": 0,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def _blank_pdf() -> bytes:
	import io

	from pypdf import PdfWriter

	writer, out = PdfWriter(), io.BytesIO()
	writer.add_blank_page(width=595, height=842)
	writer.write(out)
	return out.getvalue()


def attach_supplier_invoice(invoice_name, content=None):
	"""Attach a stand-in for the supplier's own invoice, which the Finance Assistant's review asks for."""
	content = content or _blank_pdf()
	return (
		frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"{invoice_name}-supplier-invoice.pdf",
				"attached_to_doctype": "Purchase Invoice",
				"attached_to_name": invoice_name,
				"content": content,
				"is_private": 1,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)
