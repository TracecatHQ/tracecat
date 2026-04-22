"use client"

import { useCallback, useMemo } from "react"
import type {
  CaseDurationDefinitionRead,
  CaseDurationDefinitionUpdate,
} from "@/client"
import {
  buildFieldFilters,
  CaseDurationDialog,
  type CaseDurationFormValues,
  createEmptyCaseDurationFormValues,
  getFilterFieldKey,
  normalizeFilterValues,
} from "@/components/cases/case-duration-dialog"
import { isCaseDropdownEventType } from "@/components/cases/case-duration-options"

interface UpdateCaseDurationDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  duration: CaseDurationDefinitionRead | null
  onUpdateDuration: (
    durationId: string,
    payload: CaseDurationDefinitionUpdate
  ) => Promise<void>
  isUpdating?: boolean
}

function extractAnchorFormValues(
  anchor: CaseDurationDefinitionRead["start_anchor"]
): CaseDurationFormValues["start"] {
  if (isCaseDropdownEventType(anchor.event_type)) {
    const defId = anchor.field_filters?.["data.definition_id"]
    const optionIds = normalizeFilterValues(
      anchor.field_filters?.["data.new_option_id"]
    )
    return {
      selection: anchor.selection ?? "first",
      eventType: anchor.event_type,
      filterValues: [],
      dropdownDefinitionId: typeof defId === "string" ? defId : undefined,
      dropdownOptionIds: optionIds,
    }
  }

  const fieldKey = getFilterFieldKey(anchor.event_type)
  const filterValues = normalizeFilterValues(
    fieldKey ? anchor.field_filters?.[fieldKey] : undefined
  )
  return {
    selection: anchor.selection ?? "first",
    eventType: anchor.event_type,
    filterValues,
    dropdownDefinitionId: undefined,
    dropdownOptionIds: [],
  }
}

const getInitialValues = (
  duration: CaseDurationDefinitionRead | null
): CaseDurationFormValues | undefined => {
  if (!duration) {
    return undefined
  }

  return {
    name: duration.name,
    description: duration.description ?? "",
    start: extractAnchorFormValues(duration.start_anchor),
    end: extractAnchorFormValues(duration.end_anchor),
  }
}

export function UpdateCaseDurationDialog({
  open,
  onOpenChange,
  duration,
  onUpdateDuration,
  isUpdating = false,
}: UpdateCaseDurationDialogProps) {
  const initialValues = useMemo(
    () => getInitialValues(duration) ?? createEmptyCaseDurationFormValues(),
    [duration]
  )

  const handleSubmit = useCallback(
    async (values: CaseDurationFormValues) => {
      if (!duration) {
        return
      }

      const startFieldFilters = buildFieldFilters(
        values.start.eventType,
        values.start.filterValues,
        values.start
      )
      const endFieldFilters = buildFieldFilters(
        values.end.eventType,
        values.end.filterValues,
        values.end
      )

      const payload: CaseDurationDefinitionUpdate = {
        name: values.name.trim(),
        description: values.description?.trim() || null,
        start_anchor: {
          event_type: values.start.eventType,
          selection: values.start.selection,
          timestamp_path: "created_at",
          ...(startFieldFilters ? { field_filters: startFieldFilters } : {}),
        },
        end_anchor: {
          event_type: values.end.eventType,
          selection: values.end.selection,
          timestamp_path: "created_at",
          ...(endFieldFilters ? { field_filters: endFieldFilters } : {}),
        },
      }

      try {
        await onUpdateDuration(duration.id, payload)
        onOpenChange(false)
      } catch (error) {
        console.error("Failed to update case duration definition", error)
      }
    },
    [duration, onOpenChange, onUpdateDuration]
  )

  return (
    <CaseDurationDialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) {
          onOpenChange(false)
        } else {
          onOpenChange(true)
        }
      }}
      title="Update duration"
      description="Modify this duration metric."
      submitLabel={isUpdating ? "Saving..." : "Save changes"}
      isSubmitting={isUpdating}
      initialValues={initialValues}
      onSubmit={handleSubmit}
    />
  )
}
