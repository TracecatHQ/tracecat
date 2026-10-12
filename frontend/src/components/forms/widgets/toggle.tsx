"use client"

import {
  describedBy,
  type FormWidgetProps,
} from "@/components/forms/widgets/types"
import { Switch } from "@/components/ui/switch"

/** On/off switch. Submits a boolean; off is a valid answer. */
export function ToggleWidget({
  id,
  value,
  onChange,
  onBlur,
  disabled,
  invalid,
}: FormWidgetProps) {
  return (
    <Switch
      id={id}
      size="lg"
      checked={value === true}
      onCheckedChange={onChange}
      onBlur={onBlur}
      disabled={disabled}
      aria-invalid={invalid}
      aria-labelledby={`${id}-label`}
      aria-describedby={describedBy(id)}
      className="shadow-none"
    />
  )
}
