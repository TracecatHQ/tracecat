"use client"

import { formControlClass } from "@/components/forms/form-styles"
import {
  describedBy,
  type FormWidgetProps,
} from "@/components/forms/widgets/types"
import { Input } from "@/components/ui/input"

/** Single-line text. Submits a string. */
export function TextWidget({
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
      value={typeof value === "string" ? value : ""}
      onChange={(event) => onChange(event.target.value)}
      onBlur={onBlur}
      disabled={disabled}
      placeholder={field.placeholder ?? undefined}
      aria-invalid={invalid}
      aria-describedby={describedBy(id)}
      className={formControlClass(invalid)}
    />
  )
}
