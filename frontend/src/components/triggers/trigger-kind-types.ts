import type { LucideIcon } from "lucide-react"
import type { ComponentType } from "react"
import type { TriggerCreate, TriggerKind, TriggerRead } from "@/client"
import type { EntitlementKey } from "@/hooks/use-entitlements"

/** The config of a new trigger, a union over trigger kinds. */
export type TriggerConfig = TriggerCreate["config"]

/** Props the create dialog passes to a trigger kind's settings form. */
export interface TriggerKindFormProps {
  /** The dialog's submit button points at a `<form>` with this ID. */
  formId: string
  /** True from submit until the trigger is created or fails. */
  pending: boolean
  /**
   * Runs one submission. `resolveConfig` does the kind's own requests first
   * (such as creating a source) and returns the config; the dialog holds
   * every control disabled until it and the trigger create finish. Throws on
   * failure; the form shows the error and keeps its values.
   */
  onSubmit: (resolveConfig: () => Promise<TriggerConfig>) => Promise<void>
}

/** Props the trigger detail page passes to a trigger kind's settings view. */
export interface TriggerKindDetailsProps {
  workspaceId: string
  trigger: TriggerRead
}

/**
 * Everything the Triggers tab needs to know about one trigger kind. The list,
 * the create dialog and the detail page read only this, so a new kind is a
 * new entry in `TRIGGER_KINDS` plus its form and details components.
 */
export interface TriggerKindDefinition {
  kind: TriggerKind
  label: string
  /** One line for the kind picker. */
  description: string
  icon: LucideIcon
  /** The plan feature the kind needs, or `null` for every plan. */
  entitlement: EntitlementKey | null
  /** Placeholder for the create dialog's name field. */
  namePlaceholder: string
  /** The toast line after create: what to do next. */
  createdHint: string
  /** What the delete confirmation says happens. */
  deleteDescription: string
  /**
   * Set when the kind's targets are fixed: why. The detail page then shows
   * this instead of the controls to add, switch or remove targets.
   */
  fixedTargets?: string
  /** Where events come from, for the list's "Source" column. */
  describeSource: (trigger: TriggerRead) => string
  CreateForm: ComponentType<TriggerKindFormProps>
  Details: ComponentType<TriggerKindDetailsProps>
}
