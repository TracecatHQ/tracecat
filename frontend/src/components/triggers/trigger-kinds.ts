import { CalendarClockIcon, DatabaseIcon } from "lucide-react"
import type { TriggerKind, TriggerRead } from "@/client"
import { ObjectStorageTriggerDetails } from "@/components/triggers/object-storage/object-storage-trigger-details"
import { ObjectStorageTriggerForm } from "@/components/triggers/object-storage/object-storage-trigger-form"
import {
  describeSchedule,
  ScheduleTriggerDetails,
} from "@/components/triggers/schedule/schedule-trigger-details"
import { ScheduleTriggerForm } from "@/components/triggers/schedule/schedule-trigger-form"
import type { TriggerKindDefinition } from "@/components/triggers/trigger-kind-types"

/**
 * Every trigger kind the Triggers tab can create and show. Adding a kind is
 * one entry here plus its form and details components; the list, the create
 * dialog and the detail page read nothing else.
 */
export const TRIGGER_KINDS: Record<TriggerKind, TriggerKindDefinition> = {
  schedule: {
    kind: "schedule",
    label: "Schedule",
    description: "Runs a workflow on a cron expression or a fixed interval.",
    icon: CalendarClockIcon,
    entitlement: null,
    namePlaceholder: "Nightly report",
    createdHint: "Put it online to start running the workflow.",
    deleteDescription:
      "This deletes the workflow's schedule. The workflow stops running on it.",
    fixedTargets:
      "A schedule runs the workflow it was created for. To run another workflow, create another schedule.",
    describeSource: describeSchedule,
    CreateForm: ScheduleTriggerForm,
    Details: ScheduleTriggerDetails,
  },
  object_storage: {
    kind: "object_storage",
    label: "Object storage",
    description: "Runs when a new object lands in an S3 bucket, through SNS.",
    icon: DatabaseIcon,
    entitlement: "object_triggers",
    namePlaceholder: "New uploads",
    createdHint: "Add a target, then put the trigger online.",
    deleteDescription:
      "Its targets are removed too. The event source stays, so other triggers can keep using it.",
    describeSource: (trigger: TriggerRead) => {
      if (trigger.config.kind !== "object_storage") {
        return ""
      }
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
  // Own keys only: an unknown kind like "toString" must not hit the prototype.
  if (!Object.prototype.hasOwnProperty.call(TRIGGER_KINDS, trigger.kind)) {
    return undefined
  }
  return TRIGGER_KINDS[trigger.kind]
}
