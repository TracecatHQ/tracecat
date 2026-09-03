import { Check, CircleSlash } from "lucide-react"
import { type CSSProperties, useCallback, useState } from "react"
import { FormProvider, useForm, useFormContext } from "react-hook-form"
import { z } from "zod"
import type { CaseFieldRead, CaseUpdate } from "@/client"
import {
  ExpandFieldCell,
  JsonFieldDialog,
  LongTextFieldDialog,
} from "@/components/cases/case-field-kind-dialogs"
import {
  UrlFieldPopover,
  type UrlFieldValue,
} from "@/components/cases/case-url-field-popover"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { CheckIndicator } from "@/components/ui/check-indicator"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from "@/components/ui/form"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
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
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { useOverflowBadges } from "@/hooks/use-overflow-badges"
import { getCaseFieldEditorValue } from "@/lib/case-field-display"
import { cn, linearStyles } from "@/lib/utils"

const customFieldFormSchema = z.object({
  id: z.string(),
  value: z.unknown(),
})

type CustomFieldFormSchema = z.infer<typeof customFieldFormSchema>

export function CustomField({
  customField,
  updateCase,
  inputClassName,
  inputStyle,
  onValueChange,
  formClassName,
}: {
  customField: CaseFieldRead
  updateCase: (caseUpdate: Partial<CaseUpdate>) => Promise<void>
  inputClassName?: string
  inputStyle?: CSSProperties
  onValueChange?: (id: string, value: unknown) => void
  formClassName?: string
}) {
  // Kind-specific fields use dialog-based editing, not inline blur-to-save
  if (customField.kind === "LONG_TEXT") {
    return (
      <LongTextCustomField customField={customField} updateCase={updateCase} />
    )
  }
  if (customField.kind === "URL") {
    return <UrlCustomField customField={customField} updateCase={updateCase} />
  }
  // Plain JSONB fields (no kind) use a JSON editor dialog
  if (customField.type === "JSONB") {
    return <JsonCustomField customField={customField} updateCase={updateCase} />
  }

  return (
    <InlineCustomField
      key={`${customField.id}-${JSON.stringify(customField.value)}`}
      customField={customField}
      updateCase={updateCase}
      inputClassName={inputClassName}
      inputStyle={inputStyle}
      onValueChange={onValueChange}
      formClassName={formClassName}
    />
  )
}

/** Standard inline-edit custom field (blur-to-save via form). */
function InlineCustomField({
  customField,
  updateCase,
  inputClassName,
  inputStyle,
  onValueChange,
  formClassName,
}: {
  customField: CaseFieldRead
  updateCase: (caseUpdate: Partial<CaseUpdate>) => Promise<void>
  inputClassName?: string
  inputStyle?: CSSProperties
  onValueChange?: (id: string, value: unknown) => void
  formClassName?: string
}) {
  const form = useForm<CustomFieldFormSchema>({
    defaultValues: {
      id: customField.id,
      value:
        customField.type === "NUMERIC" || customField.type === "INTEGER"
          ? getCaseFieldEditorValue(customField.value, customField.type)
          : customField.value,
    },
  })

  const onSubmit = async (data: CustomFieldFormSchema) => {
    try {
      await updateCase({ fields: { [customField.id]: data.value } })
    } catch (error) {
      console.error(error)
    }
  }
  const onBlur = useCallback(
    (id: string, value: unknown) => {
      onValueChange?.(id, value)
      form.setValue("value", value)
      form.handleSubmit(onSubmit)()
    },
    [form, onSubmit, onValueChange]
  )
  return (
    <FormProvider {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className={formClassName}>
        <CustomFieldInner
          customField={customField}
          onBlur={onBlur}
          inputClassName={inputClassName}
          inputStyle={inputStyle}
        />
      </form>
    </FormProvider>
  )
}

function LongTextCustomField({
  customField,
  updateCase,
}: {
  customField: CaseFieldRead
  updateCase: (caseUpdate: Partial<CaseUpdate>) => Promise<void>
}) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const currentValue =
    typeof customField.value === "string" ? customField.value : ""

  const handleSave = useCallback(
    async (value: string) => {
      try {
        await updateCase({
          fields: { [customField.id]: value || null },
        })
      } catch (error) {
        console.error(error)
      }
    },
    [customField.id, updateCase]
  )

  return (
    <>
      <ExpandFieldCell
        onClick={() => setDialogOpen(true)}
        hasValue={currentValue.length > 0}
      />
      <LongTextFieldDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        fieldLabel={customField.display_name}
        initialValue={currentValue}
        onSave={handleSave}
      />
    </>
  )
}

function UrlCustomField({
  customField,
  updateCase,
}: {
  customField: CaseFieldRead
  updateCase: (caseUpdate: Partial<CaseUpdate>) => Promise<void>
}) {
  const parsed =
    customField.value &&
    typeof customField.value === "object" &&
    !Array.isArray(customField.value)
      ? (customField.value as { url?: string; label?: string })
      : null
  const urlValue: UrlFieldValue = {
    url: parsed?.url ?? "",
    label: parsed?.label ?? "",
  }

  const handleSave = useCallback(
    async (value: UrlFieldValue | null) => {
      try {
        await updateCase({
          fields: { [customField.id]: value },
        })
      } catch (error) {
        console.error(error)
      }
    },
    [customField.id, updateCase]
  )

  return (
    <UrlFieldPopover
      fieldLabel={customField.display_name}
      value={urlValue.url ? urlValue : null}
      onSave={handleSave}
    />
  )
}

function JsonCustomField({
  customField,
  updateCase,
}: {
  customField: CaseFieldRead
  updateCase: (caseUpdate: Partial<CaseUpdate>) => Promise<void>
}) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const hasValue = customField.value !== null && customField.value !== undefined

  const handleSave = useCallback(
    async (value: unknown) => {
      try {
        await updateCase({
          fields: { [customField.id]: value },
        })
      } catch (error) {
        console.error(error)
      }
    },
    [customField.id, updateCase]
  )

  return (
    <>
      <ExpandFieldCell
        onClick={() => setDialogOpen(true)}
        hasValue={hasValue}
      />
      <JsonFieldDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        fieldLabel={customField.display_name}
        initialValue={customField.value}
        onSave={handleSave}
      />
    </>
  )
}

interface CustomFieldProps {
  customField: CaseFieldRead
  onBlur?: (id: string, value: unknown) => void
  inputClassName?: string
  inputStyle?: CSSProperties
}

/**
 * Sentinel Select item value for the boolean field's clear row. Radix Select
 * forbids empty-string item values, so the clear item carries this marker and
 * `onValueChange` translates it to `null` before anything is persisted.
 */
const CLEAR_SELECT_VALUE = "__clear__"

/**
 * Divider plus pinned "Clear" row rendered as a sibling of a cmdk `Command`
 * inside a popover, so the search filter can never hide it and it never
 * scrolls away with the option list. Clears the field back to null.
 */
function ClearFieldRow({
  fieldLabel,
  onClear,
}: {
  fieldLabel: string
  onClear: () => void
}) {
  return (
    <>
      <Separator />
      <div className="p-1">
        <button
          type="button"
          aria-label={`Clear ${fieldLabel} field`}
          className="flex w-full cursor-default select-none items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-none hover:bg-accent hover:text-accent-foreground focus-visible:bg-accent focus-visible:text-accent-foreground"
          onClick={onClear}
        >
          <CircleSlash
            className="h-3 w-3 shrink-0 text-muted-foreground"
            aria-hidden
          />
          Clear
        </button>
      </div>
    </>
  )
}

/** Map a boolean field value to its controlled Select value ("" when unset). */
function booleanSelectValue(value: unknown): "true" | "false" | "" {
  if (value === true) return "true"
  if (value === false) return "false"
  return ""
}

/** Display label for a boolean field value; empty string when unset. */
function booleanDisplayLabel(value: unknown): string {
  if (value === true) return "True"
  if (value === false) return "False"
  return ""
}

/**
 * Renders badges in a single-line container with overflow detection.
 * When badges overflow, shows only those that fit plus a "+N" indicator.
 *
 * A hidden measurement div always renders every badge so the
 * ResizeObserver can re-expand the visible set when the container grows.
 */
function MultiSelectBadges({ values }: { values: string[] }) {
  // gap-1 = 0.25rem = 4px between badges.
  const { measureRef, visibleCount } = useOverflowBadges(values, { gap: 4 })

  const hiddenCount = values.length - visibleCount

  return (
    <div className="relative overflow-hidden">
      {/* Hidden measurement layer — all badges + a +N placeholder for width calculation */}
      <div
        ref={measureRef}
        aria-hidden
        className="pointer-events-none flex items-center gap-1"
        style={{
          visibility: "hidden",
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
        }}
      >
        {values.map((value) => (
          <Badge
            key={value}
            variant="secondary"
            className="shrink-0 px-1.5 py-0 text-[11px]"
          >
            {value}
          </Badge>
        ))}
        <span className="shrink-0 text-[11px]">+{values.length}</span>
      </div>
      {/* Visible layer — only the badges that fit plus the +N indicator */}
      <div className="flex items-center gap-1">
        {values.slice(0, visibleCount).map((value) => (
          <Badge
            key={value}
            variant="secondary"
            className="shrink-0 px-1.5 py-0 text-[11px]"
          >
            {value}
          </Badge>
        ))}
        {hiddenCount > 0 && (
          <span className="shrink-0 text-[11px] text-muted-foreground">
            +{hiddenCount}
          </span>
        )}
      </div>
    </div>
  )
}

/**
 * Core renderer that dispatches on field type.
 */
export function CustomFieldInner({
  customField,
  onBlur,
  inputClassName,
  inputStyle,
}: CustomFieldProps) {
  const form = useFormContext<CustomFieldFormSchema>()
  const baseInputClassName = cn(
    linearStyles.input.full,
    "w-full min-w-0 text-right",
    inputClassName
  )

  switch (customField.type) {
    case "TEXT":
      return (
        <FormField
          control={form.control}
          name="value"
          render={({ field }) => (
            <FormItem>
              <FormControl>
                <Input
                  type="text"
                  {...field}
                  placeholder="Empty"
                  value={String(field.value || "")}
                  className={baseInputClassName}
                  style={inputStyle}
                  onBlur={() => onBlur && onBlur(customField.id, field.value)}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      )
    case "NUMERIC":
    case "INTEGER":
      return (
        <FormField
          control={form.control}
          name="value"
          render={({ field }) => (
            <FormItem>
              <FormControl>
                <Input
                  type="text"
                  inputMode={
                    customField.type === "INTEGER" ? "numeric" : "decimal"
                  }
                  value={
                    field.value === null || field.value === undefined
                      ? ""
                      : String(field.value)
                  }
                  placeholder="Empty"
                  onChange={(e) => field.onChange(e.target.value)}
                  className={baseInputClassName}
                  style={inputStyle}
                  onBlur={() => {
                    field.onBlur()
                    const raw = String(field.value ?? "").trim()
                    if (!raw) {
                      onBlur?.(customField.id, null)
                      return
                    }

                    if (customField.type === "INTEGER") {
                      if (!/^-?\d+$/.test(raw)) {
                        // Silently revert to saved value
                        field.onChange(
                          getCaseFieldEditorValue(
                            customField.value,
                            customField.type
                          )
                        )
                        return
                      }
                      const parsed = Number.parseInt(raw, 10)
                      onBlur?.(customField.id, parsed)
                      return
                    }

                    const parsed = Number(raw)
                    if (!Number.isFinite(parsed)) {
                      // Silently revert to saved value
                      field.onChange(
                        getCaseFieldEditorValue(
                          customField.value,
                          customField.type
                        )
                      )
                      return
                    }
                    onBlur?.(customField.id, raw)
                  }}
                />
              </FormControl>
            </FormItem>
          )}
        />
      )
    case "BOOLEAN":
      return (
        <FormField
          control={form.control}
          name="value"
          render={({ field }) => {
            const hasValue = field.value === true || field.value === false
            return (
              <FormItem>
                <FormControl>
                  <Select
                    value={booleanSelectValue(field.value)}
                    onValueChange={(value) => {
                      if (value === CLEAR_SELECT_VALUE) {
                        field.onChange(null)
                        onBlur?.(customField.id, null)
                        return
                      }
                      const next = value === "true"
                      field.onChange(next)
                      onBlur?.(customField.id, next)
                    }}
                  >
                    <SelectTrigger
                      className={cn(
                        linearStyles.trigger.base,
                        "h-7 w-full justify-end px-2 text-sm [&>span]:w-full [&>svg]:hidden"
                      )}
                      style={inputStyle}
                    >
                      <SelectValue
                        placeholder={
                          <div className="flex w-full items-center justify-end text-right text-sm">
                            <span className="text-muted-foreground">Empty</span>
                          </div>
                        }
                      >
                        <div className="flex w-full items-center justify-end text-right text-sm">
                          <span>{booleanDisplayLabel(field.value)}</span>
                        </div>
                      </SelectValue>
                    </SelectTrigger>
                    <SelectContent align="end">
                      <SelectItem value="true">
                        <span className="text-sm">True</span>
                      </SelectItem>
                      <SelectItem value="false">
                        <span className="text-sm">False</span>
                      </SelectItem>
                      {hasValue && (
                        <>
                          <SelectSeparator />
                          <SelectItem
                            value={CLEAR_SELECT_VALUE}
                            aria-label={`Clear ${customField.display_name} field`}
                            // Radix sets aria-labelledby on every item, which
                            // outranks aria-label; drop it so the label wins.
                            aria-labelledby={undefined}
                          >
                            <span className="flex items-center gap-2 text-sm">
                              <CircleSlash
                                className="h-3 w-3 shrink-0 text-muted-foreground"
                                aria-hidden
                              />
                              Clear
                            </span>
                          </SelectItem>
                        </>
                      )}
                    </SelectContent>
                  </Select>
                </FormControl>
                <FormMessage />
              </FormItem>
            )
          }}
        />
      )
    // JSONB fields are handled by JsonCustomField before reaching this switch
    case "TIMESTAMPTZ":
      return (
        <FormField
          control={form.control}
          name="value"
          render={({ field }) => (
            <FormItem>
              <FormControl>
                <Input
                  type="text"
                  placeholder="YYYY-MM-DDTHH:mm:ss.Z"
                  value={
                    field.value === null || field.value === undefined
                      ? ""
                      : String(field.value)
                  }
                  onChange={(e) => field.onChange(e.target.value)}
                  className={cn(
                    baseInputClassName,
                    !field.value && "text-muted-foreground"
                  )}
                  style={inputStyle}
                  onBlur={() => {
                    field.onBlur()
                    const raw = String(field.value ?? "").trim()
                    if (!raw) {
                      onBlur?.(customField.id, null)
                      return
                    }
                    const d = new Date(raw)
                    if (Number.isNaN(d.getTime())) {
                      field.onChange(customField.value)
                      return
                    }
                    onBlur?.(customField.id, d.toISOString())
                  }}
                />
              </FormControl>
            </FormItem>
          )}
        />
      )
    case "DATE":
      return (
        <FormField
          control={form.control}
          name="value"
          render={({ field }) => (
            <FormItem>
              <FormControl>
                <Input
                  type="text"
                  placeholder="YYYY-MM-DD"
                  value={
                    field.value === null || field.value === undefined
                      ? ""
                      : String(field.value)
                  }
                  onChange={(e) => field.onChange(e.target.value)}
                  className={cn(
                    baseInputClassName,
                    !field.value && "text-muted-foreground"
                  )}
                  style={inputStyle}
                  onBlur={() => {
                    field.onBlur()
                    const raw = String(field.value ?? "").trim()
                    if (!raw) {
                      onBlur?.(customField.id, null)
                      return
                    }
                    const d = new Date(raw)
                    if (Number.isNaN(d.getTime())) {
                      field.onChange(customField.value)
                      return
                    }
                    const yyyy = d.getUTCFullYear()
                    const mm = String(d.getUTCMonth() + 1).padStart(2, "0")
                    const dd = String(d.getUTCDate()).padStart(2, "0")
                    onBlur?.(customField.id, `${yyyy}-${mm}-${dd}`)
                  }}
                />
              </FormControl>
            </FormItem>
          )}
        />
      )
    case "SELECT": {
      const options = customField.options ?? []
      return (
        <FormField
          control={form.control}
          name="value"
          render={({ field }) => {
            const currentValue =
              typeof field.value === "string" ? field.value : ""
            const hasValue = currentValue.length > 0
            return (
              <FormItem>
                <Popover>
                  <PopoverTrigger asChild>
                    <FormControl>
                      <Button
                        variant="ghost"
                        role="combobox"
                        className={cn(
                          linearStyles.input.full,
                          "inline-flex h-7 w-full min-w-0 justify-end gap-1 whitespace-nowrap rounded-sm border-none px-2 text-right text-sm font-normal shadow-none",
                          !currentValue && "text-muted-foreground",
                          inputClassName
                        )}
                        style={inputStyle}
                      >
                        <span className="truncate">
                          {currentValue || "Select..."}
                        </span>
                      </Button>
                    </FormControl>
                  </PopoverTrigger>
                  <PopoverContent className="w-56 p-0" align="end">
                    <Command>
                      <CommandInput
                        placeholder="Search..."
                        className="h-8 text-sm"
                      />
                      <CommandList className="max-h-56">
                        <CommandEmpty className="py-2 text-center text-sm">
                          No option found
                        </CommandEmpty>
                        <CommandGroup>
                          {options.map((option) => (
                            <CommandItem
                              key={option}
                              value={option}
                              className="text-sm"
                              onSelect={() => {
                                field.onChange(option)
                                onBlur?.(customField.id, option)
                              }}
                            >
                              <Check
                                className={cn(
                                  "mr-2 h-3 w-3",
                                  currentValue === option
                                    ? "opacity-100"
                                    : "opacity-0"
                                )}
                              />
                              {option}
                            </CommandItem>
                          ))}
                        </CommandGroup>
                      </CommandList>
                    </Command>
                    {hasValue && (
                      <ClearFieldRow
                        fieldLabel={customField.display_name}
                        onClear={() => {
                          field.onChange(null)
                          onBlur?.(customField.id, null)
                        }}
                      />
                    )}
                  </PopoverContent>
                </Popover>
                <FormMessage />
              </FormItem>
            )
          }}
        />
      )
    }
    case "MULTI_SELECT": {
      const options = customField.options ?? []
      return (
        <FormField
          control={form.control}
          name="value"
          render={({ field }) => {
            // Parse current values - could be array or JSON string
            let currentValues: string[] = []
            if (Array.isArray(field.value)) {
              currentValues = field.value.filter(
                (v): v is string => typeof v === "string"
              )
            } else if (typeof field.value === "string" && field.value) {
              try {
                const parsed = JSON.parse(field.value)
                if (Array.isArray(parsed)) {
                  currentValues = parsed.filter(
                    (v): v is string => typeof v === "string"
                  )
                }
              } catch {
                // Not JSON, treat as single value
                currentValues = [field.value]
              }
            }

            const hasValue = currentValues.length > 0

            const toggleOption = (option: string) => {
              const newValues = currentValues.includes(option)
                ? currentValues.filter((v) => v !== option)
                : [...currentValues, option]
              const next = newValues.length === 0 ? null : newValues
              field.onChange(next)
              onBlur?.(customField.id, next)
            }

            return (
              <FormItem>
                <Popover>
                  <HoverCard openDelay={300}>
                    <HoverCardTrigger asChild>
                      <PopoverTrigger asChild>
                        <FormControl>
                          <Button
                            variant="ghost"
                            role="combobox"
                            className={cn(
                              linearStyles.input.full,
                              "inline-flex h-7 w-full min-w-0 justify-end gap-1 overflow-hidden whitespace-nowrap rounded-sm border-none px-2 text-right text-sm font-normal shadow-none",
                              currentValues.length === 0 &&
                                "text-muted-foreground",
                              inputClassName
                            )}
                            style={inputStyle}
                          >
                            {currentValues.length === 0 ? (
                              <span className="truncate">Select...</span>
                            ) : (
                              <MultiSelectBadges values={currentValues} />
                            )}
                          </Button>
                        </FormControl>
                      </PopoverTrigger>
                    </HoverCardTrigger>
                    {currentValues.length > 0 && (
                      <HoverCardContent
                        className="w-auto max-w-xs p-2"
                        side="top"
                        align="end"
                      >
                        <div className="flex flex-wrap gap-1">
                          {currentValues.map((value) => (
                            <Badge
                              key={value}
                              variant="secondary"
                              className="text-[11px]"
                            >
                              {value}
                            </Badge>
                          ))}
                        </div>
                      </HoverCardContent>
                    )}
                  </HoverCard>
                  <PopoverContent className="w-56 p-0" align="end">
                    <Command>
                      <CommandInput
                        placeholder="Search..."
                        className="h-8 text-sm"
                      />
                      <CommandList className="max-h-56">
                        <CommandEmpty className="py-2 text-center text-sm">
                          No option found
                        </CommandEmpty>
                        <CommandGroup>
                          {options.map((option) => (
                            <CommandItem
                              key={option}
                              value={option}
                              className="group text-sm"
                              onSelect={() => toggleOption(option)}
                            >
                              <CheckIndicator
                                checked={currentValues.includes(option)}
                              />
                              {option}
                            </CommandItem>
                          ))}
                        </CommandGroup>
                      </CommandList>
                    </Command>
                    {hasValue && (
                      <ClearFieldRow
                        fieldLabel={customField.display_name}
                        onClear={() => {
                          field.onChange(null)
                          onBlur?.(customField.id, null)
                        }}
                      />
                    )}
                  </PopoverContent>
                </Popover>
                <FormMessage />
              </FormItem>
            )
          }}
        />
      )
    }
    default:
      // Fallback for unknown types - render as text input
      return (
        <FormField
          control={form.control}
          name="value"
          render={({ field }) => (
            <FormItem>
              <FormControl>
                <Input
                  type="text"
                  {...field}
                  placeholder="Empty"
                  value={String(field.value ?? "")}
                  className={baseInputClassName}
                  style={inputStyle}
                  onBlur={() => onBlur && onBlur(customField.id, field.value)}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      )
  }
}
