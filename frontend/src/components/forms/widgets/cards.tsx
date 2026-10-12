"use client"

import { CheckIcon } from "lucide-react"
import { formStyles } from "@/components/forms/form-styles"
import {
  describedBy,
  type FormWidgetProps,
} from "@/components/forms/widgets/types"
import { isAllowedImageDataUri } from "@/lib/forms/image-data-uri"
import { cn } from "@/lib/utils"

/**
 * Options as a grid of cards, each with an optional image.
 *
 * A single-value field is a radio group and submits one option value. A list
 * field lets several cards be chosen and submits a list.
 */
export function CardsWidget({
  id,
  field,
  value,
  onChange,
  onBlur,
  disabled,
  invalid,
}: FormWidgetProps) {
  const options = field.options ?? []
  const selected = new Set<unknown>(Array.isArray(value) ? value : [value])

  function handleSelect(optionValue: string) {
    if (!field.multiple) {
      onChange(optionValue)
      return
    }
    // Keep the submitted list in option order, whatever the click order.
    onChange(
      options
        .map((option) => option.value)
        .filter((candidate) =>
          candidate === optionValue
            ? !selected.has(candidate)
            : selected.has(candidate)
        )
    )
  }

  return (
    <div
      id={id}
      role={field.multiple ? "group" : "radiogroup"}
      aria-labelledby={`${id}-label`}
      aria-describedby={describedBy(id)}
      aria-invalid={invalid}
      className="grid grid-cols-1 gap-3 sm:grid-cols-3"
    >
      {options.map((option) => {
        const checked = selected.has(option.value)
        return (
          <button
            key={option.value}
            type="button"
            role={field.multiple ? "checkbox" : "radio"}
            aria-checked={checked}
            disabled={disabled}
            onClick={() => handleSelect(option.value)}
            onBlur={onBlur}
            className={cn(
              "relative flex flex-col overflow-hidden rounded-lg border bg-background text-left transition-colors",
              "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring",
              "disabled:cursor-not-allowed disabled:opacity-50",
              checked
                ? "border-primary bg-primary/5 ring-1 ring-inset ring-primary"
                : "hover:bg-muted/50",
              invalid && !checked && "border-destructive"
            )}
          >
            {isAllowedImageDataUri(option.image) && (
              // Card images are inline data URIs checked above. A plain <img>
              // keeps them out of the Next image optimizer, so no
              // `remotePatterns` or CSP change is needed.
              <img
                src={option.image}
                alt=""
                className="aspect-[4/3] w-full border-b object-cover"
              />
            )}
            {checked && (
              <span className="absolute right-2 top-2 flex size-[18px] items-center justify-center rounded-full bg-primary text-primary-foreground">
                <CheckIcon className="size-3" />
              </span>
            )}
            <span className="block px-3 pb-3 pt-2.5">
              <span className={cn(formStyles.label, "block")}>
                {option.label}
              </span>
              {option.description && (
                <span className={cn(formStyles.help, "mt-0.5 block")}>
                  {option.description}
                </span>
              )}
            </span>
          </button>
        )
      })}
    </div>
  )
}
