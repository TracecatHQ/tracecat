import { format, isValid as isValidDate } from "date-fns"
import { Check } from "lucide-react"
import {
  type CSSProperties,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react"
import { FormProvider, useForm, useFormContext } from "react-hook-form"
import { z } from "zod"
import type { CaseFieldRead, CaseUpdate } from "@/client"
import {
  JsonFieldCell,
  JsonFieldDialog,
  LongTextFieldCell,
  LongTextFieldDialog,
  UrlFieldCell,
  UrlFieldDialog,
} from "@/components/cases/case-field-kind-dialogs"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { DateTimePicker } from "@/components/ui/date-time-picker"
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
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { toast } from "@/components/ui/use-toast"
import { cn, linearStyles } from "@/lib/utils"

const customFieldFormSchema = z.object({
  id: z.string(),
  value: z.unknown(),
})

type CustomFieldFormSchema = z.infer<typeof customFieldFormSchema>

const DATE_TIME_DISPLAY_FORMAT = "yyyy-MM-dd HH:mm"
const DATE_DISPLAY_FORMAT = "yyyy-MM-dd"

const formatDateFieldValue = (
  date: Date,
  fieldType: "TIMESTAMP" | "TIMESTAMPTZ"
) =>
  fieldType === "TIMESTAMPTZ"
    ? date.toISOString()
    : format(date, "yyyy-MM-dd'T'HH:mm:ss")

const toDateValue = (value: unknown): Date | null => {
  if (value instanceof Date) {
    return isValidDate(value) ? value : null
  }

  if (typeof value === "string" && value.length > 0) {
    const parsed = new Date(value)
    return isValidDate(parsed) ? parsed : null
  }

  return null
}

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
      value: customField.value,
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
      <LongTextFieldCell
        onClick={() => setDialogOpen(true)}
        hasValue={currentValue.length > 0}
      />
      <LongTextFieldDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        fieldLabel={customField.id}
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
  const [dialogOpen, setDialogOpen] = useState(false)

  const parsed =
    customField.value &&
    typeof customField.value === "object" &&
    !Array.isArray(customField.value)
      ? (customField.value as { url?: string; label?: string })
      : null
  const urlValue = {
    url: parsed?.url ?? "",
    label: parsed?.label ?? "",
  }

  const handleSave = useCallback(
    async (value: { url: string; label: string }) => {
      try {
        await updateCase({
          fields: {
            [customField.id]: value.url && value.label ? value : null,
          },
        })
      } catch (error) {
        console.error(error)
      }
    },
    [customField.id, updateCase]
  )

  return (
    <>
      <UrlFieldCell
        value={urlValue.url ? urlValue : null}
        onEdit={() => setDialogOpen(true)}
      />
      <UrlFieldDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        fieldLabel={customField.id}
        initialValue={urlValue}
        onSave={handleSave}
      />
    </>
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
      <JsonFieldCell onClick={() => setDialogOpen(true)} hasValue={hasValue} />
      <JsonFieldDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        fieldLabel={customField.id}
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
 * Renders badges in a single-line container with overflow detection.
 * When badges overflow, shows only those that fit plus a "+N" indicator.
 *
 * A hidden measurement div always renders every badge so the
 * ResizeObserver can re-expand the visible set when the container grows.
 */
function MultiSelectBadges({ values }: { values: string[] }) {
  const measureRef = useRef<HTMLDivElement>(null)
  const [visibleCount, setVisibleCount] = useState(values.length)

  useEffect(() => {
    const container = measureRef.current
    if (!container) return

    const measure = () => {
      const badges = Array.from(container.children) as HTMLElement[]
      let count = 0
      for (const child of badges) {
        if (child.offsetLeft + child.offsetWidth > container.clientWidth) {
          break
        }
        count++
      }
      setVisibleCount(count > 0 ? count : 1)
    }

    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(container)
    return () => observer.disconnect()
  }, [values])

  const hiddenCount = values.length - visibleCount

  return (
    <div className="relative overflow-hidden">
      {/* Hidden measurement layer — always contains every badge */}
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
                        toast({
                          title: "Validation error",
                          description: "Must be a valid integer",
                          variant: "default",
                        })
                        return
                      }
                      onBlur?.(customField.id, Number.parseInt(raw, 10))
                      return
                    }

                    if (!/^-?(?:\d+|\d*\.\d+)$/.test(raw)) {
                      toast({
                        title: "Validation error",
                        description: "Must be a valid number",
                        variant: "default",
                      })
                      return
                    }
                    const parsed = Number(raw)
                    if (!Number.isFinite(parsed)) {
                      toast({
                        title: "Validation error",
                        description: "Must be a valid number",
                        variant: "default",
                      })
                      return
                    }
                    onBlur?.(customField.id, parsed)
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
          render={({ field }) => (
            <FormItem>
              <FormControl>
                <Select
                  value={
                    field.value === true
                      ? "true"
                      : field.value === false
                        ? "false"
                        : undefined
                  }
                  onValueChange={(value) => {
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
                    <SelectValue>
                      <div className="flex w-full items-center justify-end text-right text-sm">
                        {field.value === true ? (
                          <span>True</span>
                        ) : field.value === false ? (
                          <span>False</span>
                        ) : (
                          <span className="text-muted-foreground">Empty</span>
                        )}
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
                  </SelectContent>
                </Select>
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      )
    // JSONB fields are handled by JsonCustomField before reaching this switch
    case "TIMESTAMP":
    case "TIMESTAMPTZ": {
      const fieldType =
        customField.type === "TIMESTAMPTZ" ? "TIMESTAMPTZ" : "TIMESTAMP"
      return (
        <FormField
          control={form.control}
          name="value"
          render={({ field }) => {
            const dateValue = toDateValue(field.value)

            return (
              <FormItem>
                <FormControl>
                  <DateTimePicker
                    value={dateValue}
                    onChange={(next) => {
                      const formatted = next
                        ? formatDateFieldValue(next, fieldType)
                        : null
                      field.onChange(formatted)
                      onBlur?.(customField.id, formatted)
                    }}
                    onBlur={() => field.onBlur()}
                    formatDisplay={(date) =>
                      format(date, DATE_TIME_DISPLAY_FORMAT)
                    }
                    icon={<></>}
                    buttonProps={{
                      variant: "ghost",
                      className: cn(
                        linearStyles.input.full,
                        "inline-flex h-7 w-full min-w-0 justify-end whitespace-nowrap rounded-sm border-none px-2 text-right text-sm font-normal shadow-none",
                        !dateValue && "text-muted-foreground",
                        inputClassName
                      ),
                      style: inputStyle,
                    }}
                    popoverContentProps={{
                      className: "w-auto border-none shadow-none p-0",
                      align: "start",
                    }}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )
          }}
        />
      )
    }
    case "DATE": {
      return (
        <FormField
          control={form.control}
          name="value"
          render={({ field }) => {
            const dateValue = toDateValue(field.value)

            return (
              <FormItem>
                <FormControl>
                  <DateTimePicker
                    value={dateValue}
                    onChange={(next) => {
                      const formatted = next ? format(next, "yyyy-MM-dd") : null
                      field.onChange(formatted)
                      onBlur?.(customField.id, formatted)
                    }}
                    onBlur={() => field.onBlur()}
                    hideTime
                    placeholder="Select date"
                    formatDisplay={(date) => format(date, DATE_DISPLAY_FORMAT)}
                    icon={<></>}
                    buttonProps={{
                      variant: "ghost",
                      className: cn(
                        linearStyles.input.full,
                        "inline-flex h-7 w-full min-w-0 justify-end whitespace-nowrap rounded-sm border-none px-2 text-right text-sm font-normal shadow-none",
                        !dateValue && "text-muted-foreground",
                        inputClassName
                      ),
                      style: inputStyle,
                    }}
                    popoverContentProps={{
                      className: "w-auto border-none shadow-none p-0",
                      align: "end",
                    }}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )
          }}
        />
      )
    }
    case "SELECT": {
      const options = customField.options ?? []
      return (
        <FormField
          control={form.control}
          name="value"
          render={({ field }) => {
            const currentValue =
              typeof field.value === "string" ? field.value : ""
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
                      <CommandList>
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

            const toggleOption = (option: string) => {
              const newValues = currentValues.includes(option)
                ? currentValues.filter((v) => v !== option)
                : [...currentValues, option]
              field.onChange(newValues)
              onBlur?.(customField.id, newValues)
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
                      <CommandList>
                        <CommandEmpty className="py-2 text-center text-sm">
                          No option found
                        </CommandEmpty>
                        <CommandGroup>
                          {options.map((option) => (
                            <CommandItem
                              key={option}
                              value={option}
                              className="text-sm"
                              onSelect={() => toggleOption(option)}
                            >
                              <Check
                                className={cn(
                                  "mr-2 h-4 w-4",
                                  currentValues.includes(option)
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
