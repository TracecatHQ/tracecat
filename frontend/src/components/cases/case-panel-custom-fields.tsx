import {
  useMemo,
  useState,
  type CSSProperties,
  type ChangeEvent,
} from "react"
import { FormProvider, useForm, useFormContext } from "react-hook-form"
import { CalendarClock, Clock } from "lucide-react"
import { format, isValid as isValidDate } from "date-fns"
import { z } from "zod"
import type { CaseCustomFieldRead, CaseUpdate } from "@/client"
import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import { Switch } from "@/components/ui/switch"
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
  customField: CaseCustomFieldRead
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
    console.log("caseUpdate", caseUpdate)
    try {
      await updateCase(caseUpdate)
    } catch (error) {
      console.error(error)
    }
  }
  const onBlur = (id: string, value: unknown) => {
    console.log("onblur", { id, value })
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
  customField: CaseCustomFieldRead
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
                    "w-auto min-w-[8ch]",
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
                    "w-auto min-w-[8ch]",
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
                    "w-auto min-w-[8ch]",
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
    case "TIMESTAMPTZ":
      return (
        <FormField
          control={form.control}
          name="value"
          render={({ field }) => (
            <FormItem>
              <FormControl>
                <DateTimePicker
                  value={field.value}
                  onValueChange={(next) => {
                    field.onChange(next ?? null)
                    onBlur?.(customField.id, next ?? null)
                  }}
                  onOpenChange={(open) => {
                    if (!open) {
                      field.onBlur()
                    }
                  }}
                  inputClassName={inputClassName}
                  inputStyle={inputStyle}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      )
  }
}

interface DateTimePickerProps {
  value: unknown
  onValueChange: (value: string | null) => void
  onOpenChange?: (open: boolean) => void
  inputClassName?: string
  inputStyle?: CSSProperties
}

function DateTimePicker({
  value,
  onValueChange,
  onOpenChange,
  inputClassName,
  inputStyle,
}: DateTimePickerProps) {
  const [open, setOpen] = useState(false)
  const stringValue = useMemo(() => {
    if (typeof value === "string") return value
    if (value instanceof Date) return value.toISOString()
    return ""
  }, [value])
  const dateValue = useMemo(() => {
    if (!stringValue) return undefined
    const parsed = new Date(stringValue)
    return isValidDate(parsed) ? parsed : undefined
  }, [stringValue])

  const handleSelect = (date: Date | undefined) => {
    if (!date) {
      onValueChange(null)
      return
    }

    const next = new Date(date)
    if (dateValue) {
      next.setHours(dateValue.getHours(), dateValue.getMinutes(), 0, 0)
    }
    onValueChange(next.toISOString())
  }

  const handleTimeChange = (event: ChangeEvent<HTMLInputElement>) => {
    if (!dateValue) return
    const [hoursStr = "", minutesStr = ""] = event.target.value.split(":")
    const hours = Number.parseInt(hoursStr, 10)
    const minutes = Number.parseInt(minutesStr, 10)
    if (Number.isNaN(hours) || Number.isNaN(minutes)) return

    const next = new Date(dateValue)
    next.setHours(hours, minutes, 0, 0)
    onValueChange(next.toISOString())
  }

  const handleSetNow = () => {
    const now = new Date()
    onValueChange(now.toISOString())
    setOpen(false)
    onOpenChange?.(false)
  }

  const handleClear = () => {
    onValueChange(null)
    setOpen(false)
    onOpenChange?.(false)
  }

  const displayValue =
    dateValue && isValidDate(dateValue)
      ? format(dateValue, "MMM d yyyy '·' p")
      : "Select date and time"

  return (
    <Popover
      open={open}
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen)
        onOpenChange?.(nextOpen)
      }}
    >
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          className={cn(
            linearStyles.input.full,
            "inline-flex min-w-[8ch] justify-start whitespace-nowrap rounded-sm text-left text-xs font-normal border-none shadow-none",
            !dateValue && "text-muted-foreground",
            inputClassName
          )}
          style={inputStyle}
        >
          <CalendarClock className="mr-2 size-4" />
          {displayValue}
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className="w-auto border-none shadow-none p-0"
        align="start"
      >
        <Calendar
          mode="single"
          selected={dateValue}
          onSelect={handleSelect}
          initialFocus
        />
        <div className="flex flex-col gap-2 border-t border-border p-3">
          <Input
            type="time"
            value={dateValue ? format(dateValue, "HH:mm") : ""}
            onChange={handleTimeChange}
            step={60}
            disabled={!dateValue}
          />
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              className="flex-1 text-xs"
              onClick={handleSetNow}
            >
              <Clock className="mr-2 size-4" />
              Now
            </Button>
            <Button
              type="button"
              variant="ghost"
              className="flex-1 text-xs text-muted-foreground"
              onClick={handleClear}
              disabled={!stringValue}
            >
              Clear
            </Button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  )
}
