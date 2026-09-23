// The shapes the server returns, stated once.
//
// Each type names the Python function that produces it, and components map onto these keys 1:1 --
// deliberately, so a payload change breaks the typecheck or a component rather than quietly
// producing a wrong screen. These are asserted, not verified at runtime.

export type Tone = "ok" | "warn" | "danger" | "info" | "plain";

// --- folt_tasks.my_tasks -------------------------------------------------------------------------

export type TaskRow = {
  doctype: string;
  name: string;
  title: string;
  state: string | null;
  lane: number | null;
  of: number;
  step_label: string | null;
  waiting_on: string[];
  owner: string;
  owner_name: string;
  modified: string;
  age_days: number;
  amount: number | null;
  currency_field: string | null;
};

export type TaskGroup = {
  key: string;
  doctype: string;
  step_label: string | null;
  lane: number | null;
  of: number;
  waiting_on: string[];
  rows: TaskRow[];
};

export type Bucket = "awaiting" | "drafts" | "approved" | "archives";

export type Tasks = {
  bucket: Bucket;
  counts: Record<Bucket, number>;
  groups: TaskGroup[];
  total: number;
};

// --- spa.catalogue ------------------------------------------------------------------------------

export type Lane = {
  rank: number;
  label: string;
  states: string[];
  optional: string[];
  roles: string[];
  terminal: boolean;
};

export type Workflow = {
  doctype: string;
  label: string;
  lanes: Lane[];
  off_path: Record<string, { kind: string; roles: string[]; custodian: string | null }>;
  chain: { step: number; of: number; title: string } | null;
  counts: Record<string, number>;
  tones: Record<string, Tone>;
  can_create: boolean;
};

// --- spa.documents --------------------------------------------------------------------------------

export type DocumentList = { rows: TaskRow[]; more: boolean };

// --- document_guide.get_guide (inside spa.document) ----------------------------------------------

export type Step = {
  rank: number;
  label: string;
  states: string[];
  optional: string[];
  roles: string[];
  custodian: string[];
  docstatus: number;
  terminal: boolean;
  status: "done" | "current" | "ahead";
};

export type TimelineEntry = {
  state: string;
  at: string;
  by: string;
  by_name: string;
  kind: "raised" | "forward" | "turned_down" | "derived" | "finished" | "note";
  reason: string | null;
  content?: string | null;
};

export type Evidence = {
  fieldname: string;
  label: string;
  description: string | null;
  attached: boolean;
  url: string | null;
  required_at: string[];
  advisory: boolean;
  blocks_next: boolean;
  blocks: string[];
};

export type Handoff = {
  label: string;
  target: string;
  method: string;
  arg: string;
  description: string;
  ready: boolean;
  ready_at: string;
  existing: string[];
};

export type Guide = {
  doctype: string;
  name: string;
  state: string | null;
  docstatus: number;
  steps: Step[];
  lane: number | null;
  of: number;
  at_optional: string | null;
  off_path: { kind: string; roles: string[]; custodian: string | null; state: string } | null;
  chain: { step: number; of: number; step_title: string } | null;
  handoffs: Handoff[];
  note?: string | null;
  waiting_for: { roles: string[]; approvers: { user: string; full_name: string; role: string }[]; unassigned: boolean };
  can_act: boolean;
  timeline: TimelineEntry[];
  documents: Evidence[];
  blocked_by: string[];
  rejection_reason: string | null;
};

// --- spa.document ---------------------------------------------------------------------------------

export type Action = {
  action: string;
  label: string;
  next_state: string;
  kind: "forward" | "turn_down" | "correction";
  needs_reason: boolean;
  submits: boolean;
  blocks: string[];
  primary: boolean;
};

export type FieldValue = string | number | null;

export type FieldSpec = {
  fieldname: string;
  label: string;
  fieldtype: string;
  reqd: boolean;
  description: string | null;
  options: string | null;
  choices: string[] | null;
  value?: FieldValue;
  display?: string | null;
  currency?: string | null;
};

export type TableRow = {
  name: string;
  idx: number;
  values: Record<string, FieldValue>;
  display: Record<string, string | null>;
  editable: boolean;
};

export type TableSpec = {
  fieldname: string;
  label: string;
  columns: FieldSpec[];
  show: FieldSpec[];
  rows: TableRow[];
  add: boolean;
  remove: boolean;
  own_rows: boolean;
  new_row: Record<string, FieldValue>;
};

export type Form = {
  editable: boolean;
  act_only: boolean;
  why_not: string | null;
  custodians: string[];
  fields: FieldSpec[];
  virtual: FieldSpec[];
  tables: TableSpec[];
  show_if: Record<string, Record<string, FieldValue>>;
  tools: string[];
};

export type SummarySection = {
  label: string;
  currency: string | null;
  fields?: FieldSpec[];
  table?: { columns: FieldSpec[]; rows: { name: string; values: Record<string, FieldValue>; display: Record<string, string | null> }[] };
};

export type Doc = {
  doctype: string;
  name: string;
  title: string;
  modified: string;
  docstatus: number;
  owner: string;
  owner_name: string;
  state: string | null;
  tones: Record<string, Tone>;
  guide: Guide;
  actions: Action[];
  form: Form;
  summary: SummarySection[];
  context: {
    authority?: Record<string, FieldValue>[];
    bids?: { supplier_quotation: string; supplier: string; grand_total: number; currency: string; valid_till: string | null }[];
    me_on_committee?: boolean;
    float?: Record<string, FieldValue>;
  };
  desk_url: string;
};

export type NewForm = Form & { doctype: string; label: string };

// --- spa.options ---------------------------------------------------------------------------------

export type PickerOption = { value: string; label: string; hint: string; extra?: Record<string, FieldValue> };
export type PickerResult = { allowed: boolean; reason: string | null; options: PickerOption[] };

// --- spa.handoff ---------------------------------------------------------------------------------

export type Float = { name: string; employee_name: string; advance_amount: number; paid_amount: number; workflow_state: string };

export type HandoffResult =
  | { doctype: string; name: string; label: string; lines: string[]; needs_float?: undefined }
  | { needs_float: true; activity: string; floats: Float[] };

// --- the patch the SPA sends ---------------------------------------------------------------------

export type RowOps = {
  update?: ({ name: string } & Record<string, FieldValue>)[];
  add?: Record<string, FieldValue>[];
  remove?: string[];
};

export type Patch = Record<string, FieldValue | RowOps>;

// --- notifications.bell ---------------------------------------------------------------------------

export type Notice = {
  name: string;
  subject: string;
  email_content: string | null;
  type: string;
  document_type: string | null;
  document_name: string | null;
  from_user: string | null;
  from_user_name: string | null;
  link: string | null;
  read: number;
  creation: string;
};

export type Bell = { unread: number; logs: Notice[] };
