// The ten doctypes /folt serves (spa.py SPA_DOCTYPES), their URL slugs and what people call them.
//
// The slug is in the URL because a name alone does not say which doctype it belongs to -- REQ-…
// and PRL-… only look distinct by convention, and a Salary Slip is "Sal Slip/HR-EMP-00002/00002".
// The everyday names are FoLT's own words for these documents (the SOP's), not frappe's doctype
// names: nobody at FoLT calls a float an "Employee Advance".

export type DoctypeInfo = { doctype: string; slug: string; noun: string; plural: string; chain: "activity" | "procurement" | "payroll" };

export const DOCTYPES: DoctypeInfo[] = [
  { doctype: "Activity Requisition", slug: "activity-requisition", noun: "Activity requisition", plural: "Activity requisitions", chain: "activity" },
  { doctype: "Employee Advance", slug: "float", noun: "Float request", plural: "Float requests", chain: "activity" },
  { doctype: "Activity Participant List", slug: "attendance-register", noun: "Attendance register", plural: "Attendance registers", chain: "activity" },
  { doctype: "Participant Reimbursement List", slug: "reimbursement-list", noun: "Reimbursement list", plural: "Reimbursement lists", chain: "activity" },
  { doctype: "Expense Claim", slug: "float-retirement", noun: "Float retirement", plural: "Float retirements", chain: "activity" },
  { doctype: "Supplier Quotation", slug: "bid", noun: "Supplier bid", plural: "Supplier bids", chain: "procurement" },
  { doctype: "Procurement Committee Evaluation", slug: "committee-evaluation", noun: "Committee evaluation", plural: "Committee evaluations", chain: "procurement" },
  { doctype: "Derogation Waiver Request", slug: "waiver", noun: "Waiver request", plural: "Waiver requests", chain: "procurement" },
  { doctype: "Purchase Order", slug: "purchase-order", noun: "Purchase order", plural: "Purchase orders", chain: "procurement" },
  { doctype: "Salary Slip", slug: "payroll", noun: "Salary slip", plural: "Salary slips", chain: "payroll" },
];

const BY_SLUG = new Map(DOCTYPES.map((d) => [d.slug, d]));
const BY_DOCTYPE = new Map(DOCTYPES.map((d) => [d.doctype, d]));

export function fromSlug(slug: string): DoctypeInfo | undefined {
  return BY_SLUG.get(slug);
}

export function info(doctype: string): DoctypeInfo {
  return BY_DOCTYPE.get(doctype) ?? { doctype, slug: doctype, noun: doctype, plural: doctype, chain: "activity" };
}

export function docPath(doctype: string, name: string): string {
  // The name keeps its slashes (see router.ts); each segment is encoded on its own so a space or
  // a # in a name cannot turn into a fragment or a second path.
  return `/d/${info(doctype).slug}/${name.split("/").map(encodeURIComponent).join("/")}`;
}

export function listPath(doctype: string): string {
  return `/w/${info(doctype).slug}`;
}
