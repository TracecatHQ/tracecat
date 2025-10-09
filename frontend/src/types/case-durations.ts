export type CaseDurationAnchorSelection = "first" | "last"

export type CaseEventType =
  | "case_created"
  | "case_updated"
  | "case_closed"
  | "case_reopened"
  | "priority_changed"
  | "severity_changed"
  | "status_changed"
  | "fields_changed"
  | "assignee_changed"
  | "attachment_created"
  | "attachment_deleted"
  | "payload_changed"

export interface CaseDurationEventAnchor {
  event_type: CaseEventType
  timestamp_path: string
  field_filters: Record<string, unknown>
  selection: CaseDurationAnchorSelection
}

export interface CaseDurationBase {
  name: string
  description: string | null
  start_anchor: CaseDurationEventAnchor
  end_anchor: CaseDurationEventAnchor
}

export interface CaseDurationRead extends CaseDurationBase {
  id: string
}

export interface CaseDurationCreate {
  name: string
  description?: string | null
  start_anchor: CaseDurationEventAnchor
  end_anchor: CaseDurationEventAnchor
}
