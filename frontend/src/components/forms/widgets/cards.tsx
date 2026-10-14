"use client"

import { CheckIcon } from "lucide-react"
import { type KeyboardEvent, useMemo, useRef } from "react"
import { formStyles } from "@/components/forms/form-styles"
import { controlProps, type FormWidgetProps } from "@/components/forms/widgets"
import { isAllowedImageDataUri } from "@/lib/forms/image-data-uri"
import { cn } from "@/lib/utils"

const ARROW_STEPS: Record<string, number> = {
  ArrowRight: 1,
  ArrowDown: 1,
  ArrowLeft: -1,
  ArrowUp: -1,
}

/**
 * Options as a grid of cards, each with an optional image.
 *
 * A single-value field is a radio group: one tab stop, arrow keys move to and
 * select the neighbouring card, and it submits one option value. A list field
 * is a group of checkboxes, each its own tab stop, and submits a list.
 */
export function CardsWidget(props: FormWidgetProps) {
  const { field, value, onChange, disabled, invalid } = props
  const options = field.options ?? []
  const selected = new Set<unknown>(Array.isArray(value) ? value : [value])
  const cardRefs = useRef<(HTMLButtonElement | null)[]>([])
  // Checking an image decodes part of it, so do it once per option list and
  // not on every keystroke elsewhere in the form.
  const images = useMemo(
    () =>
      (field.options ?? []).map((option) =>
        isAllowedImageDataUri(option.image) ? option.image : null
      ),
    [field.options]
  )

  function handleSelect(optionValue: string) {
    if (!field.multiple) return onChange(optionValue)
    // Toggle the clicked option. The list keeps option order, whatever the
    // click order.
    onChange(
      options
        .map((option) => option.value)
        .filter(
          (candidate) => (candidate === optionValue) !== selected.has(candidate)
        )
    )
  }

  // Radio groups have a single tab stop: the chosen card, or the first one.
  const checkedIndex = options.findIndex((option) => selected.has(option.value))
  const tabStopIndex = checkedIndex === -1 ? 0 : checkedIndex

  function handleKeyDown(event: KeyboardEvent, index: number) {
    const step = ARROW_STEPS[event.key]
    if (field.multiple || !step) return
    event.preventDefault()
    const nextIndex = (index + step + options.length) % options.length
    cardRefs.current[nextIndex]?.focus()
    onChange(options[nextIndex].value)
  }

  return (
    // The columns follow the form's own width, not the viewport's, so the
    // cards stack in a narrow preview pane instead of squeezing their text.
    <div className="@container">
      <div
        {...controlProps(props)}
        role={field.multiple ? "group" : "radiogroup"}
        aria-labelledby={`${props.id}-label`}
        aria-required={field.multiple ? undefined : field.required}
        className="grid grid-cols-1 gap-3 @md:grid-cols-3"
      >
        {options.map((option, index) => {
          const checked = selected.has(option.value)
          const image = images[index]
          return (
            <button
              key={option.value}
              ref={(element) => {
                cardRefs.current[index] = element
              }}
              type="button"
              role={field.multiple ? "checkbox" : "radio"}
              aria-checked={checked}
              tabIndex={field.multiple || index === tabStopIndex ? 0 : -1}
              disabled={disabled}
              onClick={() => handleSelect(option.value)}
              onKeyDown={(event) => handleKeyDown(event, index)}
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
              {image && (
                // Card images are inline data URIs checked above. A plain <img>
                // keeps them out of the Next image optimizer, so no
                // `remotePatterns` or CSP change is needed.
                <img
                  src={image}
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
    </div>
  )
}
