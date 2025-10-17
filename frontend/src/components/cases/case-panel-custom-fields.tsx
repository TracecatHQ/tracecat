import { format, isValid as isValidDate } from "date-fns"
import type { CSSProperties } from "react"
import { FormProvider, useForm, useFormContext } from "react-hook-form"
import { z } from "zod"
import type { CaseCustomFieldRead, CaseUpdate } from "@/client"
import { DateTimePicker } from "@/components/ui/date-time-picker"
import {
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
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
  }
}
