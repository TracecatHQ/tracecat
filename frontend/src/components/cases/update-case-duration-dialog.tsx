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
  type CaseDurationAnchorEventType,
  isCaseDropdownEventType,
  isCaseDurationAnchorEventType,
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
  anchor: CaseDurationDefinitionRead["start_anchor"],
  fallbackEventType: CaseDurationAnchorEventType
): CaseDurationFormValues["start"] {
  const eventType = isCaseDurationAnchorEventType(anchor.event_type)
    ? anchor.event_type
    : fallbackEventType

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
    start: extractAnchorFormValues(duration.start_anchor, "case_created"),
    end: extractAnchorFormValues(duration.end_anchor, "case_closed"),
  }
}

function areStringArraysEqual(
  left: string[] | undefined,
  right: string[] | undefined
): boolean {
  if (left === right) {
    return true
  }
  if (!left || !right || left.length !== right.length) {
    return false
  }
  return left.every((value, index) => value === right[index])
}

function areAnchorFormValuesEqual(
  left: CaseDurationFormValues["start"],
  right: CaseDurationFormValues["start"]
): boolean {
  return (
    left.selection === right.selection &&
    left.eventType === right.eventType &&
    left.dropdownDefinitionId === right.dropdownDefinitionId &&
    areStringArraysEqual(left.filterValues, right.filterValues) &&
    areStringArraysEqual(left.dropdownOptionIds, right.dropdownOptionIds)
  )
}

function buildAnchorPayload(
  anchor: CaseDurationFormValues["start"],
  filters: CaseDurationEventFilters | null | undefined
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
      const shouldSendStartAnchor =
        isCaseDurationAnchorEventType(duration.start_anchor.event_type) ||
        !areAnchorFormValuesEqual(values.start, initialValues.start)
      const shouldSendEndAnchor =
        isCaseDurationAnchorEventType(duration.end_anchor.event_type) ||
        !areAnchorFormValuesEqual(values.end, initialValues.end)

      const payload: CaseDurationDefinitionUpdate = {
        name: values.name.trim(),
        description: values.description?.trim() || null,
      }
      if (shouldSendStartAnchor) {
        payload.start_anchor = buildAnchorPayload(values.start, startFilters)
      }
      if (shouldSendEndAnchor) {
        payload.end_anchor = buildAnchorPayload(values.end, endFilters)
      }

      try {
        await onUpdateDuration(duration.id, payload)
        onOpenChange(false)
      } catch (error) {
        console.error("Failed to update case duration definition", error)
      }
    },
    [duration, initialValues, onOpenChange, onUpdateDuration]
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
