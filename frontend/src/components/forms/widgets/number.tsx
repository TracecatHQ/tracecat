"use client"

import { formControlClass } from "@/components/forms/form-styles"
import {
  describedBy,
  type FormWidgetProps,
} from "@/components/forms/widgets/types"
import { Input } from "@/components/ui/input"

/** Numeric input. Submits a number, or nothing while empty. */
export function NumberWidget({
  id,
  field,
  value,
  onChange,
  onBlur,
  disabled,
  invalid,
}: FormWidgetProps) {
  return (
    <Input
      id={id}
      type="number"
      inputMode="decimal"
      value={typeof value === "number" && !Number.isNaN(value) ? value : ""}
      onChange={(event) => {
        const raw = event.target.value
        onChange(raw === "" ? null : Number(raw))
      }}
      onBlur={onBlur}
      disabled={disabled}
      placeholder={field.placeholder ?? undefined}
      aria-invalid={invalid}
      aria-required={field.required}
      aria-describedby={describedBy(id, field, invalid)}
      className={formControlClass(invalid)}
    />
  )
}
