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

const getInitialValues = (
  duration: CaseDurationDefinitionRead | null
): CaseDurationFormValues | undefined => {
  if (!duration) {
    return undefined
  }

  const startFieldKey = getFilterFieldKey(duration.start_anchor.event_type)
  const endFieldKey = getFilterFieldKey(duration.end_anchor.event_type)

  const startFilters = normalizeFilterValues(
    startFieldKey
      ? duration.start_anchor.field_filters?.[startFieldKey]
      : undefined
  )
  const endFilters = normalizeFilterValues(
    endFieldKey ? duration.end_anchor.field_filters?.[endFieldKey] : undefined
  )

  return {
    name: duration.name,
    description: duration.description ?? "",
    start: {
      selection: duration.start_anchor.selection ?? "first",
      eventType: duration.start_anchor.event_type,
      filterValues: startFilters,
    },
    end: {
      selection: duration.end_anchor.selection ?? "first",
      eventType: duration.end_anchor.event_type,
      filterValues: endFilters,
    },
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
        values.start.filterValues
      )
      const endFieldFilters = buildFieldFilters(
        values.end.eventType,
        values.end.filterValues
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
