"use client"

import { format, isValid, parse } from "date-fns"
import { CalendarIcon } from "lucide-react"
import { useMemo, useState } from "react"
import type { FormFieldBlock } from "@/client"
import { formControlClass, formStyles } from "@/components/forms/form-styles"
import { MultiTagCommandInput, type Suggestion } from "@/components/tags-input"
import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import type { FormFileUploader } from "@/lib/forms/files"
import { cn } from "@/lib/utils"

/** Props every form widget takes. */
export interface FormWidgetProps {
  /**
   * DOM id of the control. The field row renders `${id}-label`, and
   * `${id}-help` and `${id}-error` when there is help or an error.
   */
  id: string
  field: FormFieldBlock
  value: unknown
  onChange: (value: unknown) => void
  /**
   * Set the value from the one form state holds when the change applies, not
   * the `value` this render saw. For changes that land later, such as an
   * upload finishing after the widget remounted.
   */
  updateValue: (update: (current: unknown) => unknown) => void
  disabled: boolean
  invalid: boolean
  formId: string
  workspaceId: string
  /** Set only on a live form. Without it, picked files stay local. */
  uploadFile?: FormFileUploader
}

/**
 * The id and ARIA state of a widget's control: whether it is invalid, and the
 * help and error text it is described by, naming only text that is shown.
 */
export function controlProps({ id, field, invalid }: FormWidgetProps) {
  const describedBy = [field.help && `${id}-help`, invalid && `${id}-error`]
    .filter(Boolean)
    .join(" ")
  return {
    id,
    "aria-invalid": invalid,
    "aria-describedby": describedBy || undefined,
  }
}

/**
 * Text box for `text` and `long_text`, which submit a string, and `number`,
 * which submits a number, or nothing while empty.
 */
export function TextWidget(props: FormWidgetProps) {
  const { field, value, onChange, disabled, invalid } = props
  const shared = {
    ...controlProps(props),
    disabled,
    placeholder: field.placeholder ?? undefined,
    "aria-required": field.required,
    className: formControlClass(invalid),
  }
  if (field.widget === "number") {
    let shown = ""
    if (
      typeof value === "string" ||
      (typeof value === "number" && !Number.isNaN(value))
    ) {
      shown = String(value)
    }
    return (
      <Input
        {...shared}
        type="number"
        inputMode="decimal"
        value={shown}
        onChange={({ target }) => {
          if (target.value === "") {
            onChange(null)
            return
          }
          // Past 2^53 a whole number loses digits. Its text is sent instead,
          // and the server reads it exactly.
          const number = Number(target.value)
          onChange(
            Number.isInteger(number) && !Number.isSafeInteger(number)
              ? target.value
              : number
          )
        }}
      />
    )
  }
  const text = typeof value === "string" ? value : ""
  if (field.widget === "long_text") {
    return (
      <Textarea
        {...shared}
        value={text}
        onChange={({ target }) => onChange(target.value)}
        className={formControlClass(invalid, "h-24 resize-y leading-5")}
      />
    )
  }
  return (
    <Input
      {...shared}
      value={text}
      onChange={({ target }) => onChange(target.value)}
    />
  )
}

/** On/off switch. Submits a boolean; off is a valid answer. */
export function ToggleWidget(props: FormWidgetProps) {
  return (
    <Switch
      {...controlProps(props)}
      size="lg"
      checked={props.value === true}
      onCheckedChange={props.onChange}
      disabled={props.disabled}
      aria-labelledby={`${props.id}-label`}
      className="shadow-none"
    />
  )
}

/** Dropdown of the field's options. Submits the chosen option's value. */
export function SelectWidget(props: FormWidgetProps) {
  const { field, value, onChange, disabled, invalid } = props
  // Radix reserves the empty string for "no selection", so option values are
  // JSON-serialised, as in `workflow-trigger-form.tsx`.
  return (
    <Select
      value={typeof value === "string" ? JSON.stringify(value) : ""}
      onValueChange={(next) => onChange(JSON.parse(next))}
      disabled={disabled}
    >
      <SelectTrigger
        {...controlProps(props)}
        aria-required={field.required}
        className={formControlClass(invalid)}
      >
        <SelectValue placeholder={field.placeholder ?? "Select an option"} />
      </SelectTrigger>
      <SelectContent>
        {(field.options ?? []).map((option) => (
          <SelectItem
            key={option.value}
            value={JSON.stringify(option.value)}
            className={formStyles.option}
          >
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

const OPTION_SEARCH_KEYS: (keyof Suggestion)[] = ["label", "value"]
const TAG_SEARCH_KEYS: (keyof Suggestion)[] = ["label"]

/**
 * Several of the field's options (`multi_select`), or free-text tags added
 * with Enter (`tags`). Submits a list of strings.
 */
export function TagsWidget(props: FormWidgetProps) {
  const { field, value, onChange, disabled, invalid } = props
  const tags = field.widget === "tags"
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
  const { id, ...aria } = controlProps(props)
  return (
    <MultiTagCommandInput
      inputId={id}
      inputAria={{ ...aria, "aria-required": field.required }}
      value={Array.isArray(value) ? value : []}
      onChange={onChange}
      suggestions={suggestions}
      searchKeys={tags ? TAG_SEARCH_KEYS : OPTION_SEARCH_KEYS}
      allowCustomTags={tags}
      disableSuggestions={tags}
      placeholder={
        field.placeholder ?? (tags ? "Type and press Enter" : "Select options")
      }
      disabled={disabled}
      // The inner text input hardcodes 12 px; raise it to 14 px from here.
      inputClassName={cn(
        "min-h-9 py-1.5 text-sm shadow-none [&_input]:text-sm",
        invalid && "border-destructive"
      )}
    />
  )
}

const DATE_FORMAT = "yyyy-MM-dd"

function parseDate(value: unknown, withTime: boolean): Date | undefined {
  if (typeof value !== "string") return undefined
  // A plain date is read as a local date, so the day never shifts across
  // time zones.
  const date = withTime
    ? new Date(value)
    : parse(value, DATE_FORMAT, new Date())
  return isValid(date) ? date : undefined
}

/**
 * Calendar picker. `date` submits `YYYY-MM-DD`, with no time or time zone.
 * `datetime` adds a time input: the requester picks in local time and it
 * submits an ISO 8601 instant in UTC.
 */
export function DateWidget(props: FormWidgetProps) {
  const { id, field, value, onChange, disabled, invalid } = props
  const withTime = field.widget === "datetime"
  const [open, setOpen] = useState(false)
  const date = parseDate(value, withTime)

  function handleSelect(selected: Date | undefined) {
    setOpen(false)
    if (!selected) return onChange(null)
    if (!withTime) return onChange(format(selected, DATE_FORMAT))
    // Keep the time already chosen; a first pick starts at midnight.
    const next = new Date(selected)
    next.setHours(date?.getHours() ?? 0, date?.getMinutes() ?? 0, 0, 0)
    onChange(next.toISOString())
  }

  function handleTimeChange(time: string) {
    // Sets the time on the picked day, with seconds at zero.
    const next = date && parse(time, "HH:mm", date)
    if (next && isValid(next)) onChange(next.toISOString())
  }

  const picker = (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          {...controlProps(props)}
          type="button"
          variant="outline"
          disabled={disabled}
          // Named by the field label and the text it shows, so the picked date
          // is announced. The label alone would hide it.
          aria-labelledby={`${id}-label ${id}-value`}
          className={formControlClass(
            invalid,
            cn(
              "justify-start gap-2 px-3 font-normal",
              withTime ? "min-w-0 flex-1" : "w-full",
              !date && "text-muted-foreground"
            )
          )}
        >
          <CalendarIcon className="size-4 text-muted-foreground" />
          <span id={`${id}-value`}>
            {date
              ? format(date, "MMM d, yyyy")
              : (field.placeholder ?? "Pick a date")}
          </span>
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-auto overflow-hidden p-0" align="start">
        <Calendar
          mode="single"
          selected={date}
          defaultMonth={date}
          captionLayout="dropdown"
          onSelect={handleSelect}
        />
      </PopoverContent>
    </Popover>
  )
  if (!withTime) return picker
  return (
    <div className="flex gap-2">
      {picker}
      <Input
        type="time"
        aria-label={`${field.label} time`}
        value={date ? format(date, "HH:mm") : ""}
        onChange={(event) => handleTimeChange(event.target.value)}
        disabled={disabled || !date}
        className={formControlClass(
          invalid,
          "w-28 flex-none appearance-none bg-background [&::-webkit-calendar-picker-indicator]:hidden"
        )}
      />
    </div>
  )
}
