import { DatabaseIcon } from "lucide-react"
import type { TriggerKind, TriggerRead } from "@/client"
import { ObjectStorageTriggerDetails } from "@/components/triggers/object-storage/object-storage-trigger-details"
import { ObjectStorageTriggerForm } from "@/components/triggers/object-storage/object-storage-trigger-form"
import type { TriggerKindDefinition } from "@/components/triggers/trigger-kind-types"

/**
 * Every trigger kind the Triggers tab can create and show. Adding a kind is
 * one entry here plus its form and details components; the list, the create
 * dialog and the detail page read nothing else.
 */
export const TRIGGER_KINDS: Record<TriggerKind, TriggerKindDefinition> = {
  object_storage: {
    kind: "object_storage",
    label: "Object storage",
    description: "Runs when a new object lands in an S3 bucket, through SNS.",
    icon: DatabaseIcon,
    describeSource: (trigger) => {
      const prefix = trigger.config.key_prefix
      return prefix
        ? `${trigger.config.source_name} · ${prefix}`
        : trigger.config.source_name
    },
    CreateForm: ObjectStorageTriggerForm,
    Details: ObjectStorageTriggerDetails,
  },
}

/** Kinds in picker order. */
export const TRIGGER_KIND_LIST: TriggerKindDefinition[] =
  Object.values(TRIGGER_KINDS)

/**
 * The definition for a trigger's kind, or `undefined` for a kind this build
 * does not know (a newer API).
 */
export function getTriggerKind(
  trigger: Pick<TriggerRead, "kind">
): TriggerKindDefinition | undefined {
  return (TRIGGER_KINDS as Partial<Record<string, TriggerKindDefinition>>)[
    trigger.kind
  ]
}
