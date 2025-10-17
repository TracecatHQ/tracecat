"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useQueryClient } from "@tanstack/react-query"
import { format } from "date-fns"
import { CalendarClock, Clock } from "lucide-react"
import type { ChangeEvent } from "react"
import { useEffect, useState } from "react"
import { type ControllerRenderProps, useForm } from "react-hook-form"
import { z } from "zod"
import { casesCreateField } from "@/client"
import { SqlTypeDisplay } from "@/components/data-type/sql-type-display"
import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
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
import { type SqlTypeCreatable, SqlTypeCreatableEnum } from "@/lib/tables"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

const caseFieldFormSchema = z.object({
  name: z
    .string()
    .min(1, "Field name is required")
    .max(100, "Field name must be less than 100 characters")
    .refine(
      (value) => /^[a-zA-Z][a-zA-Z0-9_]*$/.test(value),
      "Field name must start with a letter and contain only letters, numbers, and underscores"
    ),
  type: z.enum(SqlTypeCreatableEnum),
  nullable: z.boolean().default(true),
  default: z.string().nullable().optional(),
})

type CaseFieldFormValues = z.infer<typeof caseFieldFormSchema>

interface AddCustomFieldDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function AddCustomFieldDialog({
  open,
  onOpenChange,
}: AddCustomFieldDialogProps) {
  const workspaceId = useWorkspaceId()
  const queryClient = useQueryClient()
  const [isSubmitting, setIsSubmitting] = useState(false)

  const form = useForm<CaseFieldFormValues>({
    resolver: zodResolver(caseFieldFormSchema),
    defaultValues: {
      name: "",
      type: "TEXT",
      nullable: true,
      default: null,
    },
  })
  const selectedType = form.watch("type")

  useEffect(() => {
    form.setValue("default", "")
    form.clearErrors("default")
  }, [form, selectedType])

  const onSubmit = async (data: CaseFieldFormValues) => {
    setIsSubmitting(true)
    try {
      let defaultValue: string | number | boolean | null = null
      const rawDefault = data.default

      if (
        rawDefault !== null &&
        rawDefault !== undefined &&
        rawDefault !== ""
      ) {
        switch (data.type) {
          case "INTEGER": {
            const normalized =
              typeof rawDefault === "string" ? rawDefault.trim() : rawDefault
            if (typeof normalized === "string" && normalized.length === 0) {
              form.setError("default", {
                type: "manual",
                message: "Default must be a whole number",
              })
              setIsSubmitting(false)
              return
            }
            const parsed =
              typeof normalized === "number" ? normalized : Number(normalized)
            if (!Number.isInteger(parsed)) {
              form.setError("default", {
                type: "manual",
                message: "Default must be a whole number",
              })
              setIsSubmitting(false)
              return
            }
            defaultValue = parsed
            break
          }
          case "NUMERIC": {
            const parsed =
              typeof rawDefault === "number" ? rawDefault : Number(rawDefault)
            if (Number.isNaN(parsed)) {
              form.setError("default", {
                type: "manual",
                message: "Default must be a number",
              })
              setIsSubmitting(false)
              return
            }
            defaultValue = parsed
            break
          }
          case "BOOLEAN": {
            const normalized = String(rawDefault).trim().toLowerCase()
            if (normalized === "true" || normalized === "1") {
              defaultValue = true
            } else if (normalized === "false" || normalized === "0") {
              defaultValue = false
            } else {
              form.setError("default", {
                type: "manual",
                message: "Use true, false, 1, or 0",
              })
              setIsSubmitting(false)
              return
            }
            break
          }
          case "TIMESTAMPTZ": {
            const iso =
              typeof rawDefault === "string"
                ? rawDefault
                : new Date(rawDefault).toISOString()
            const parsed = new Date(iso)
            if (Number.isNaN(parsed.getTime())) {
              form.setError("default", {
                type: "manual",
                message: "Select a valid date and time",
              })
              setIsSubmitting(false)
              return
            }
            defaultValue = parsed.toISOString()
            break
          }
          default: {
            defaultValue = String(rawDefault)
          }
        }
      }

      await casesCreateField({
        workspaceId,
        requestBody: {
          name: data.name,
          type: data.type,
          nullable: data.nullable,
          default: defaultValue,
        },
      })

      queryClient.invalidateQueries({
        queryKey: ["case-fields", workspaceId],
      })

      toast({
        title: "Field created",
        description: "The case field was created successfully.",
      })

      form.reset()
      onOpenChange(false)
    } catch (error) {
      console.error("Failed to create case field", error)
      toast({
        title: "Error creating field",
        description: "Failed to create the case field. Please try again.",
        variant: "destructive",
      })
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle>Add custom field</DialogTitle>
          <DialogDescription>
            Create a new custom field for cases.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Identifier / Slug</FormLabel>
                  <FormControl>
                    <Input {...field} />
                  </FormControl>
                  <FormDescription>
                    A human readable ID of the field. Use snake_case for best
                    compatibility.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="type"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Data type</FormLabel>
                  <Select onValueChange={field.onChange} value={field.value}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder="Select a data type" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {SqlTypeCreatableEnum.filter(
                        (type) => type !== "JSONB"
                      ).map((type) => (
                        <SelectItem key={type} value={type}>
                          <SqlTypeDisplay
                            type={type}
                            labelClassName="text-xs"
                          />
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormDescription>
                    The SQL data type for this field.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="default"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Default value (optional)</FormLabel>
                  <FormControl>
                    <DefaultValueInput type={selectedType} field={field} />
                  </FormControl>
                  <FormDescription>
                    {getDefaultHelperText(selectedType)}
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <DialogFooter>
              <Button type="submit" disabled={isSubmitting}>
                Add field
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

function getDefaultHelperText(type: SqlTypeCreatable | undefined) {
  switch (type) {
    case "INTEGER":
      return "Optional whole number that fills in missing values."
    case "NUMERIC":
      return "Optional numeric value (decimals allowed) used when none is provided."
    case "BOOLEAN":
      return "Accepts true, false, 1, or 0. Leave blank to omit a default."
    case "TIMESTAMPTZ":
      return "Select an ISO8601 date and time (stored in UTC)."
    default:
      return "Optional text used when no value is supplied."
  }
}

function DefaultValueInput({
  type,
  field,
}: {
  type: SqlTypeCreatable | undefined
  field: ControllerRenderProps<CaseFieldFormValues, "default">
}) {
  const resolvedType: SqlTypeCreatable = type ?? "TEXT"

  switch (resolvedType) {
    case "INTEGER":
      return (
        <Input
          type="number"
          step={1}
          value={field.value ?? ""}
          onChange={(event) => field.onChange(event.target.value)}
          placeholder="Enter an integer"
        />
      )
    case "NUMERIC":
      return (
        <Input
          type="number"
          step="any"
          value={field.value ?? ""}
          onChange={(event) => field.onChange(event.target.value)}
          placeholder="Enter a number"
        />
      )
    case "BOOLEAN":
      return (
        <Input
          type="text"
          value={field.value ?? ""}
          onChange={(event) => field.onChange(event.target.value)}
          placeholder="true, false, 1, or 0"
        />
      )
    case "TIMESTAMPTZ":
      return <DateTimePickerField field={field} />
    default:
      return (
        <Input
          type="text"
          value={field.value ?? ""}
          onChange={(event) => field.onChange(event.target.value)}
          placeholder="Enter default text"
        />
      )
  }
}

function DateTimePickerField({
  field,
}: {
  field: ControllerRenderProps<CaseFieldFormValues, "default">
}) {
  const [open, setOpen] = useState(false)

  const stringValue =
    typeof field.value === "string" && field.value.length > 0 ? field.value : ""
  const dateValue = stringValue ? new Date(stringValue) : undefined

  const handleSelect = (date: Date | undefined) => {
    if (!date) {
      field.onChange("")
      return
    }

    const next = new Date(date)
    const hours = dateValue?.getHours() ?? 0
    const minutes = dateValue?.getMinutes() ?? 0
    next.setHours(hours, minutes, 0, 0)
    field.onChange(next.toISOString())
  }

  const handleTimeChange = (event: ChangeEvent<HTMLInputElement>) => {
    if (!dateValue) return

    const [hoursStr = "", minutesStr = ""] = event.target.value.split(":")
    const hours = Number.parseInt(hoursStr, 10)
    const minutes = Number.parseInt(minutesStr, 10)
    if (Number.isNaN(hours) || Number.isNaN(minutes)) return

    const next = new Date(dateValue)
    next.setHours(hours, minutes, 0, 0)
    field.onChange(next.toISOString())
  }

  const handleSetNow = () => {
    const now = new Date()
    field.onChange(now.toISOString())
    setOpen(false)
  }

  const handleClear = () => {
    field.onChange("")
    setOpen(false)
  }

  return (
    <Popover
      open={open}
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen)
        if (!nextOpen) {
          field.onBlur()
        }
      }}
    >
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          className={cn(
            "w-full justify-start text-left font-normal text-sm",
            !dateValue && "text-xs text-muted-foreground"
          )}
        >
          <CalendarClock className="mr-2 size-4" />
          {dateValue ? format(dateValue, "PPP HH:mm") : "Select date and time"}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-auto p-0" align="start">
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
