"use client"

import { formControlClass, formStyles } from "@/components/forms/form-styles"
import {
  describedBy,
  type FormWidgetProps,
} from "@/components/forms/widgets/types"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

// Radix reserves the empty string for "no selection", so option values are
// JSON-serialised, as in `workflow-trigger-form.tsx`.
function serialize(value: string): string {
  return JSON.stringify(value)
}

/** Dropdown of the field's options. Submits the chosen option's value. */
export function SelectWidget({
  id,
  field,
  value,
  onChange,
  onBlur,
  disabled,
  invalid,
}: FormWidgetProps) {
  return (
    <Select
      value={typeof value === "string" ? serialize(value) : ""}
      onValueChange={(next) => onChange(JSON.parse(next))}
      disabled={disabled}
    >
      <SelectTrigger
        id={id}
        onBlur={onBlur}
        aria-invalid={invalid}
        aria-required={field.required}
        aria-describedby={describedBy(id, field, invalid)}
        className={formControlClass(invalid)}
      >
        <SelectValue placeholder={field.placeholder ?? "Select an option"} />
      </SelectTrigger>
      <SelectContent>
        {(field.options ?? []).map((option) => (
          <SelectItem
            key={option.value}
            value={serialize(option.value)}
            className={formStyles.option}
          >
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
