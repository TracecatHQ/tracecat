"use client"

import { tagInputClass } from "@/components/forms/widgets/multi-select"
import type { FormWidgetProps } from "@/components/forms/widgets/types"
import { MultiTagCommandInput, type Suggestion } from "@/components/tags-input"

const SEARCH_KEYS: (keyof Suggestion)[] = ["label"]

/** Free-text tags, added with Enter. Submits a list of strings. */
export function TagsWidget({
  id,
  field,
  value,
  onChange,
  disabled,
  invalid,
}: FormWidgetProps) {
  return (
    <MultiTagCommandInput
      inputId={id}
      value={Array.isArray(value) ? value : []}
      onChange={onChange}
      searchKeys={SEARCH_KEYS}
      allowCustomTags
      disableSuggestions
      placeholder={field.placeholder ?? "Type and press Enter"}
      disabled={disabled}
      inputClassName={tagInputClass(invalid)}
    />
  )
}
