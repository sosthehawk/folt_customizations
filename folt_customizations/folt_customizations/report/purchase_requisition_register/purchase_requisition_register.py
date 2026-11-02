"""Every purchase requisition in the period: who asked, for what activity, where its approval
stands, and how much of it has been ordered and received (ERPNext's per_ordered / per_received)."""

import frappe
from frappe import _
from frappe.utils import flt

from folt_customizations.report_common import period


def execute(filters=None):
	f = frappe._dict(filters or {})
	where = [["material_request_type", "=", "Purchase"], *period("Material Request", "transaction_date", f)]
	if f.department:
		where.append(["folt_department", "=", f.department])
	if f.workflow_state:
		where.append(["workflow_state", "=", f.workflow_state])
	rows = frappe.get_list("Material Request", filters=where, order_by="transaction_date desc, name desc", limit_page_length=0,
	                       fields=["name", "transaction_date", "schedule_date", "title", "folt_department", "owner", "folt_activity_requisition",
	                               "folt_priority", "workflow_state", "status", "folt_estimated_total", "per_ordered", "per_received"])
	columns = [
		{"fieldname": "name", "label": _("Requisition"), "fieldtype": "Link", "options": "Material Request", "width": 160},
		{"fieldname": "transaction_date", "label": _("Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "title", "label": _("What"), "fieldtype": "Data", "width": 200},
		{"fieldname": "folt_department", "label": _("Department"), "fieldtype": "Link", "options": "Department", "width": 150},
		{"fieldname": "owner", "label": _("Requested by"), "fieldtype": "Link", "options": "User", "width": 160},
		{"fieldname": "folt_activity_requisition", "label": _("Activity"), "fieldtype": "Link", "options": "Activity Requisition", "width": 130},
		{"fieldname": "folt_priority", "label": _("Priority"), "fieldtype": "Data", "width": 80},
		{"fieldname": "workflow_state", "label": _("Approval"), "fieldtype": "Data", "width": 170},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 120},
		{"fieldname": "folt_estimated_total", "label": _("Estimated"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "per_ordered", "label": _("% Ordered"), "fieldtype": "Percent", "width": 90},
		{"fieldname": "per_received", "label": _("% Received"), "fieldtype": "Percent", "width": 90},
		{"fieldname": "schedule_date", "label": _("Required by"), "fieldtype": "Date", "width": 100},
	]
	summary = [
		{"label": _("Requisitions"), "value": len(rows), "datatype": "Int"},
		{"label": _("Awaiting approval"), "value": sum(1 for r in rows if (r.workflow_state or "").startswith("Pending")), "datatype": "Int", "indicator": "Orange"},
		{"label": _("Estimated"), "value": sum(flt(r.folt_estimated_total) for r in rows), "datatype": "Currency"},
	]
	return columns, rows, None, None, summary
