"use client"

import { formControlClass } from "@/components/forms/form-styles"
import {
  describedBy,
  type FormWidgetProps,
} from "@/components/forms/widgets/types"
import { Textarea } from "@/components/ui/textarea"

/** Multi-line text. Submits a string. */
export function LongTextWidget({
  id,
  field,
  value,
  onChange,
  onBlur,
  disabled,
  invalid,
}: FormWidgetProps) {
  return (
    <Textarea
      id={id}
      value={typeof value === "string" ? value : ""}
      onChange={(event) => onChange(event.target.value)}
      onBlur={onBlur}
      disabled={disabled}
      placeholder={field.placeholder ?? undefined}
      aria-invalid={invalid}
      aria-required={field.required}
      aria-describedby={describedBy(id, field, invalid)}
      className={formControlClass(invalid, "h-24 resize-y leading-5")}
    />
  )
}
