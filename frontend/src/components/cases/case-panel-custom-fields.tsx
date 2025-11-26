import { format, isValid as isValidDate } from "date-fns"
import { Check, ChevronsUpDown, X } from "lucide-react"
import type { CSSProperties } from "react"
import { FormProvider, useForm, useFormContext } from "react-hook-form"
import { z } from "zod"
import type { CaseFieldRead, CaseUpdate } from "@/client"
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
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Switch } from "@/components/ui/switch"
import { cn, linearStyles } from "@/lib/utils"

const customFieldFormSchema = z.object({
  id: z.string(),
  value: z.unknown(),
})

type CustomFieldFormSchema = z.infer<typeof customFieldFormSchema>

const DATE_TIME_DISPLAY_FORMAT = "MMM d yyyy '·' p"

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
  const form = useForm<CustomFieldFormSchema>({
    defaultValues: {
      id: customField.id,
      value: customField.value,
    },
  })
  const onSubmit = async (data: CustomFieldFormSchema) => {
    const caseUpdate: Partial<CaseUpdate> = {
      fields: {
        [customField.id]: data.value,
      },
    }
    try {
      await updateCase(caseUpdate)
    } catch (error) {
      console.error(error)
    }
  }
  const onBlur = (id: string, value: unknown) => {
    onValueChange?.(id, value)
    form.setValue("value", value)
    form.handleSubmit(onSubmit)()
  }
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
interface CustomFieldProps {
  customField: CaseFieldRead
  onBlur?: (id: string, value: unknown) => void
  inputClassName?: string
  inputStyle?: CSSProperties
}

/**
 * We wnat to dispatch a form field
 * @param param0
 * @returns
 */
export function CustomFieldInner({
  customField,
  onBlur,
  inputClassName,
  inputStyle,
}: CustomFieldProps) {
  const form = useFormContext<CustomFieldFormSchema>()
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
                  className={cn(
                    linearStyles.input.full,
                    "inline-block w-fit min-w-[8ch]",
                    inputClassName
                  )}
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
                  type="number"
                  {...field}
                  value={Number(field.value || 0)}
                  onChange={(e) => field.onChange(Number(e.target.value))}
                  className={cn(
                    linearStyles.input.full,
                    "inline-block w-fit min-w-[8ch]",
                    inputClassName
                  )}
                  style={inputStyle}
                  onBlur={() =>
                    onBlur && onBlur(customField.id, Number(field.value))
                  }
                />
              </FormControl>
              <FormMessage />
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
                <Switch
                  checked={Boolean(field.value)}
                  onCheckedChange={(checked) => {
                    field.onChange(checked)
                    onBlur && onBlur(customField.id, checked)
                  }}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      )
    case "JSONB":
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
                  value={String(field.value || "")}
                  className={cn(
                    linearStyles.input.full,
                    "inline-block w-fit min-w-[8ch]",
                    inputClassName
                  )}
                  style={inputStyle}
                  onBlur={() => onBlur && onBlur(customField.id, field.value)}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      )
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
                    buttonProps={{
                      variant: "ghost",
                      className: cn(
                        linearStyles.input.full,
                        "inline-flex min-w-[8ch] justify-start whitespace-nowrap rounded-sm text-left text-xs font-normal border-none shadow-none",
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
                          "inline-flex min-w-[8ch] justify-between gap-1 whitespace-nowrap rounded-sm text-left text-xs font-normal border-none shadow-none",
                          !currentValue && "text-muted-foreground",
                          inputClassName
                        )}
                        style={inputStyle}
                      >
                        {currentValue || "Select..."}
                        <ChevronsUpDown className="ml-1 h-3 w-3 shrink-0 opacity-50" />
                      </Button>
                    </FormControl>
                  </PopoverTrigger>
                  <PopoverContent className="w-48 p-0" align="start">
                    <Command>
                      <CommandInput
                        placeholder="Search..."
                        className="h-8 text-xs"
                      />
                      <CommandList>
                        <CommandEmpty className="py-2 text-center text-xs">
                          No option found
                        </CommandEmpty>
                        <CommandGroup>
                          {options.map((option) => (
                            <CommandItem
                              key={option}
                              value={option}
                              className="text-xs"
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

            const removeOption = (option: string) => {
              const newValues = currentValues.filter((v) => v !== option)
              field.onChange(newValues)
              onBlur?.(customField.id, newValues)
            }

            return (
              <FormItem>
                <div className="flex flex-wrap items-center gap-1">
                  {currentValues.map((value) => (
                    <Badge
                      key={value}
                      variant="secondary"
                      className="gap-1 text-[11px]"
                    >
                      {value}
                      <button
                        type="button"
                        className="ml-0.5 rounded-full outline-none hover:bg-muted-foreground/20"
                        onClick={() => removeOption(value)}
                      >
                        <X className="h-2.5 w-2.5" />
                        <span className="sr-only">Remove {value}</span>
                      </button>
                    </Badge>
                  ))}
                  <Popover>
                    <PopoverTrigger asChild>
                      <FormControl>
                        <Button
                          variant="ghost"
                          role="combobox"
                          className={cn(
                            linearStyles.input.full,
                            "inline-flex h-6 min-w-[6ch] justify-between gap-1 whitespace-nowrap rounded-sm px-2 text-left text-xs font-normal border-none shadow-none",
                            currentValues.length === 0 &&
                              "text-muted-foreground",
                            inputClassName
                          )}
                          style={inputStyle}
                        >
                          {currentValues.length === 0 ? "Select..." : "+"}
                          <ChevronsUpDown className="h-3 w-3 shrink-0 opacity-50" />
                        </Button>
                      </FormControl>
                    </PopoverTrigger>
                    <PopoverContent className="w-48 p-0" align="start">
                      <Command>
                        <CommandInput
                          placeholder="Search..."
                          className="h-8 text-xs"
                        />
                        <CommandList>
                          <CommandEmpty className="py-2 text-center text-xs">
                            No option found
                          </CommandEmpty>
                          <CommandGroup>
                            {options.map((option) => (
                              <CommandItem
                                key={option}
                                value={option}
                                className="text-xs"
                                onSelect={() => toggleOption(option)}
                              >
                                <Check
                                  className={cn(
                                    "mr-2 h-3 w-3",
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
                </div>
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
                  className={cn(
                    linearStyles.input.full,
                    "inline-block w-fit min-w-[8ch]",
                    inputClassName
                  )}
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
