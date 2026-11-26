import type { LucideIcon } from "lucide-react"
import type { ComponentType } from "react"
import type { TriggerCreate, TriggerKind, TriggerRead } from "@/client"

/** The config of a new trigger, a union over trigger kinds. */
export type TriggerConfig = TriggerCreate["config"]

/** Props the create dialog passes to a trigger kind's settings form. */
export interface TriggerKindFormProps {
  /** The dialog's submit button points at a `<form>` with this ID. */
  formId: string
  /** Disables inputs while the dialog is submitting. */
  pending: boolean
  /**
   * Creates the trigger with this config. Throws on failure; the form shows
   * the error and keeps its values.
   */
  onSubmit: (config: TriggerConfig) => Promise<void>
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
  /** Where events come from, for the list's "Source" column. */
  describeSource: (trigger: TriggerRead) => string
  CreateForm: ComponentType<TriggerKindFormProps>
  Details: ComponentType<TriggerKindDetailsProps>
}
