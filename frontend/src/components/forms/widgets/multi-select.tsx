"use client"

import { useMemo } from "react"
import type { FormFieldBlock } from "@/client"
import {
  describedBy,
  type FormWidgetProps,
} from "@/components/forms/widgets/types"
import {
  MultiTagCommandInput,
  type MultiTagCommandInputProps,
  type Suggestion,
} from "@/components/tags-input"
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

/** ARIA state for the text input of `MultiTagCommandInput` inside a form. */
export function tagInputAria(
  id: string,
  field: FormFieldBlock,
  invalid: boolean
): MultiTagCommandInputProps["inputAria"] {
  return {
    "aria-invalid": invalid,
    "aria-required": field.required,
    "aria-describedby": describedBy(id, field, invalid),
  }
}

const SEARCH_KEYS: (keyof Suggestion)[] = ["label", "value"]

/** Pick several of the field's options. Submits a list of option values. */
export function MultiSelectWidget({
  id,
  field,
  value,
  onChange,
  onBlur,
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
      inputAria={tagInputAria(id, field, invalid)}
      onBlur={onBlur}
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
