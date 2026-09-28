"use client"

import { useCallback, useMemo } from "react"
import type {
  CaseDurationDefinitionRead,
  CaseDurationDefinitionUpdate,
  CaseDurationEventAnchor,
  CaseDurationEventFilters,
} from "@/client"
import {
  buildDurationFilters,
  CaseDurationDialog,
  type CaseDurationFormValues,
  createEmptyCaseDurationFormValues,
  normalizeFilterValues,
} from "@/components/cases/case-duration-dialog"
import {
  isCaseDropdownEventType,
  isCaseEventFilterType,
  isCaseFieldEventType,
  isCaseTagEventType,
} from "@/components/cases/case-duration-options"

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
  const eventType = anchor.event_type

  if (isCaseDropdownEventType(eventType)) {
    const defId = anchor.filters?.dropdown_definition_id
    const optionIds = normalizeFilterValues(anchor.filters?.dropdown_option_ids)
    return {
      selection: anchor.selection ?? "first",
      eventType,
      filterValues: [],
      dropdownDefinitionId: typeof defId === "string" ? defId : undefined,
      dropdownOptionIds: optionIds,
    }
  }

  const filterValues = normalizeFilterValues(getAnchorFilterValues(anchor))
  return {
    selection: anchor.selection ?? "first",
    eventType,
    filterValues,
    dropdownDefinitionId: undefined,
    dropdownOptionIds: [],
  }
}

function getAnchorFilterValues(
  anchor: CaseDurationDefinitionRead["start_anchor"]
): unknown {
  if (isCaseEventFilterType(anchor.event_type)) {
    return anchor.filters?.new_values
  }
  if (isCaseTagEventType(anchor.event_type)) {
    return anchor.filters?.tag_refs
  }
  if (isCaseFieldEventType(anchor.event_type)) {
    return anchor.filters?.field_ids
  }
  return undefined
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

function buildAnchorPayload(
  anchor: CaseDurationFormValues["start"],
  filters?: CaseDurationEventFilters | null
): CaseDurationEventAnchor {
  return {
    event_type: anchor.eventType,
    selection: anchor.selection,
    ...(filters ? { filters } : {}),
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

      const startFilters = buildDurationFilters(
        values.start.eventType,
        values.start.filterValues,
        values.start
      )
      const endFilters = buildDurationFilters(
        values.end.eventType,
        values.end.filterValues,
        values.end
      )

      const payload: CaseDurationDefinitionUpdate = {
        name: values.name.trim(),
        description: values.description?.trim() || null,
        start_anchor: buildAnchorPayload(values.start, startFilters),
        end_anchor: buildAnchorPayload(values.end, endFilters),
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
