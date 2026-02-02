export type TriggerPanelTab =
  | "trigger-webhooks"
  | "trigger-schedules"
  | "trigger-case-triggers"

export const DEFAULT_TRIGGER_PANEL_TAB: TriggerPanelTab = "trigger-webhooks"

export const TriggerPanelTabs = {
  webhook: "trigger-webhooks",
  schedules: "trigger-schedules",
  caseTriggers: "trigger-case-triggers",
} as const satisfies Record<string, TriggerPanelTab>
