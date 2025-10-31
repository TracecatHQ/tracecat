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

const normalizeFilterValues = (value: unknown): string[] => {
  if (Array.isArray(value)) {
    return value.filter((item): item is string => typeof item === "string")
  }

  if (typeof value === "string") {
    return [value]
  }

  if (
    value &&
    typeof value === "object" &&
    Array.isArray((value as { $in?: unknown[] }).$in)
  ) {
    const inArray = (value as { $in: unknown[] }).$in
    return inArray.filter((item): item is string => typeof item === "string")
  }

  return []
}

const getInitialValues = (
  duration: CaseDurationDefinitionRead | null
): CaseDurationFormValues | undefined => {
  if (!duration) {
    return undefined
  }

  const startFilters = normalizeFilterValues(
    duration.start_anchor.field_filters?.["data.new"]
  )
  const endFilters = normalizeFilterValues(
    duration.end_anchor.field_filters?.["data.new"]
  )

  return {
    name: duration.name,
    description: duration.description ?? "",
    start: {
      selection: duration.start_anchor.selection ?? "first",
      eventType: duration.start_anchor.event_type,
      filterValue: undefined,
      filterValues: startFilters,
    },
    end: {
      selection: duration.end_anchor.selection ?? "first",
      eventType: duration.end_anchor.event_type,
      filterValue: endFilters[0],
      filterValues: [],
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
        values.start.filterValue,
        values.start.filterValues
      )
      const endFieldFilters = buildFieldFilters(
        values.end.eventType,
        values.end.filterValue,
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
