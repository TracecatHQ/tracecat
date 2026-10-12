"use client"

import { useMemo } from "react"
import type { FormWidgetProps } from "@/components/forms/widgets/types"
import { MultiTagCommandInput, type Suggestion } from "@/components/tags-input"
import { cn } from "@/lib/utils"

/**
 * Size and focus classes for `MultiTagCommandInput` inside a form. The inner
 * text input hardcodes 12 px, so it is raised to 14 px from the container.
 */
export function tagInputClass(invalid: boolean): string {
  return cn(
    "min-h-9 py-1.5 text-sm shadow-none [&_input]:text-sm",
    invalid && "border-destructive"
  )
}

const SEARCH_KEYS: (keyof Suggestion)[] = ["label", "value"]

/** Pick several of the field's options. Submits a list of option values. */
export function MultiSelectWidget({
  id,
  field,
  value,
  onChange,
  disabled,
  invalid,
}: FormWidgetProps) {
  const suggestions = useMemo<Suggestion[]>(
    () =>
      (field.options ?? []).map((option) => ({
        id: option.value,
        value: option.value,
        label: option.label,
        description: option.description ?? undefined,
      })),
    [field.options]
  )
  return (
    <MultiTagCommandInput
      inputId={id}
      value={Array.isArray(value) ? value : []}
      onChange={onChange}
      suggestions={suggestions}
      searchKeys={SEARCH_KEYS}
      placeholder={field.placeholder ?? "Select options"}
      disabled={disabled}
      inputClassName={tagInputClass(invalid)}
    />
  )
}
