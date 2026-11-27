"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { ChevronDownIcon } from "lucide-react"
import { useEffect, useState } from "react"
import { type UseFormReturn, useForm } from "react-hook-form"
import { z } from "zod"
import type { ScheduleCreate } from "@/client"
import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import {
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
import { durationSchema, durationToISOString } from "@/lib/time"

/*
 * The schedule settings form, shared by the workflow page's "Create schedule"
 * dialog and the Triggers tab's schedule kind. Callers wrap the fields in
 * `<Form {...form}>` and their own `<form>`.
 */

const ISO_8601_DURATION_REGEX =
  /^P(?!$)(\d+(?:\.\d+)?Y)?(\d+(?:\.\d+)?M)?(\d+(?:\.\d+)?W)?(\d+(?:\.\d+)?D)?(T(\d+(?:\.\d+)?H)?(\d+(?:\.\d+)?M)?(\d+(?:\.\d+)?S)?)?$/

const BASIC_CRON_REGEX = /^(\S+\s+){4,5}\S+$/

const durationNumber = z.coerce.number().int().nonnegative().catch(0)

const rawDurationSchema = z.object({
  years: durationNumber,
  months: durationNumber,
  weeks: durationNumber,
  days: durationNumber,
  hours: durationNumber,
  minutes: durationNumber,
  seconds: durationNumber,
})

export const scheduleInputsSchema = z
  .object({
    mode: z.enum(["interval", "cron"]).default("interval"),
    duration: rawDurationSchema,
    cronExpression: z.string().optional(),
    timeout: z.number().optional(),
    offset: z.string().optional(),
    startAt: z.string().optional(),
    endAt: z.string().optional(),
  })
  .superRefine((values, ctx) => {
    if (values.mode === "interval") {
      try {
        // Validates at least one component and positive numbers
        durationSchema.parse(values.duration)
      } catch (error) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["duration.seconds"],
          message:
            error instanceof Error
              ? error.message
              : "Please provide a valid interval duration.",
        })
      }

      if (values.offset && values.offset.trim() !== "") {
        if (!ISO_8601_DURATION_REGEX.test(values.offset)) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ["offset"],
            message:
              "Must be a valid ISO 8601 duration string (e.g., PT1H, P1D, PT30M)",
          })
        }
      }
    } else {
      const cron = values.cronExpression?.trim()
      if (!cron) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["cronExpression"],
          message: "Cron expression is required for cron schedules.",
        })
      } else if (!BASIC_CRON_REGEX.test(cron)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["cronExpression"],
          message: "Enter a valid cron expression with 5 or 6 fields.",
        })
      }

      if (values.offset) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["offset"],
          message: "Offset is only supported for interval schedules.",
        })
      }
    }

    const startAt = values.startAt?.trim()
    const endAt = values.endAt?.trim()

    const parseDate = (value: string) => {
      const parsed = Date.parse(value)
      return Number.isNaN(parsed) ? null : parsed
    }

    if (startAt) {
      if (parseDate(startAt) === null) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["startAt"],
          message: "Enter a valid start date and time.",
        })
      }
    }

    if (endAt) {
      if (parseDate(endAt) === null) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["endAt"],
          message: "Enter a valid end date and time.",
        })
      }
    }

    if (startAt && endAt) {
      const start = parseDate(startAt)
      const end = parseDate(endAt)
      if (start !== null && end !== null && start > end) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["endAt"],
          message: "End time must be after the start time.",
        })
      }
    }
  })
type DurationType =
  | "duration.years"
  | "duration.months"
  | "duration.days"
  | "duration.hours"
  | "duration.minutes"
  | "duration.seconds"
export type ScheduleInputs = z.infer<typeof scheduleInputsSchema>

/** The schedule settings a create request carries, from the form's values. */
export type ScheduleSettings = Pick<
  ScheduleCreate,
  "every" | "offset" | "cron" | "timeout" | "start_at" | "end_at"
>

/** A form over the schedule settings, starting at a blank interval. */
export function useScheduleForm(): UseFormReturn<ScheduleInputs> {
  const form = useForm<ScheduleInputs>({
    resolver: zodResolver(scheduleInputsSchema),
    defaultValues: {
      mode: "interval",
      duration: {
        years: 0,
        months: 0,
        weeks: 0,
        days: 0,
        hours: 0,
        minutes: 0,
        seconds: 0,
      },
      cronExpression: "",
      timeout: undefined,
      offset: "",
      startAt: "",
      endAt: "",
    },
  })
  const mode = form.watch("mode")

  useEffect(() => {
    if (mode === "cron") {
      form.setValue("offset", "")
      form.clearErrors(["offset"])
    } else {
      form.clearErrors(["cronExpression"])
    }
  }, [form, mode])

  return form
}

/** Turn validated form values into the API's schedule settings. */
export function scheduleInputsToSettings(
  values: ScheduleInputs
): ScheduleSettings {
  const { mode, duration, cronExpression, timeout, offset, startAt, endAt } =
    values
  const settings: ScheduleSettings = {}

  const sanitizedTimeout =
    typeof timeout === "number" && !Number.isNaN(timeout) ? timeout : undefined
  if (sanitizedTimeout !== undefined) {
    settings.timeout = sanitizedTimeout
  }

  if (mode === "interval") {
    const parsedDuration = durationSchema.parse(duration)
    settings.every = durationToISOString(parsedDuration)
    if (offset && offset.trim() !== "") {
      settings.offset = offset
    }
  } else {
    const cron = cronExpression?.trim()
    if (cron) {
      settings.cron = cron
    }
  }

  const convertDateTime = (value?: string) => {
    const trimmed = value?.trim()
    if (!trimmed) return undefined
    const parsed = Date.parse(trimmed)
    return Number.isNaN(parsed) ? undefined : new Date(parsed).toISOString()
  }

  const startAtIso = convertDateTime(startAt)
  if (startAtIso) {
    settings.start_at = startAtIso
  }

  const endAtIso = convertDateTime(endAt)
  if (endAtIso) {
    settings.end_at = endAtIso
  }

  return settings
}

/** Schedule type, interval or cron, timeout, offset, start and end fields. */
export function ScheduleFormFields({
  form,
}: {
  form: UseFormReturn<ScheduleInputs>
}) {
  const [startDatePickerOpen, setStartDatePickerOpen] = useState(false)
  const [endDatePickerOpen, setEndDatePickerOpen] = useState(false)
  const mode = form.watch("mode")

  return (
    <div className="space-y-4">
      <FormField
        control={form.control}
        name="mode"
        render={({ field }) => (
          <FormItem>
            <FormLabel className="text-xs capitalize text-foreground/80">
              Schedule Type
            </FormLabel>
            <FormDescription className="text-xs">
              Choose between interval-based or cron-based scheduling.
            </FormDescription>
            <FormControl>
              <Select value={field.value} onValueChange={field.onChange}>
                <SelectTrigger className="text-xs capitalize">
                  <SelectValue placeholder="Select schedule type" />
                </SelectTrigger>
                <SelectContent className="text-xs">
                  <SelectItem value="interval">Interval</SelectItem>
                  <SelectItem value="cron">Cron</SelectItem>
                </SelectContent>
              </Select>
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />

      {mode === "interval" && (
        <div className="grid grid-cols-2 gap-2">
          {[
            "duration.years",
            "duration.months",
            "duration.days",
            "duration.hours",
            "duration.minutes",
            "duration.seconds",
          ].map((unit) => (
            <FormField
              key={unit}
              control={form.control}
              name={unit as DurationType}
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="text-xs capitalize text-foreground/80">
                    {unit.split(".")[1]}
                  </FormLabel>
                  <FormControl>
                    <Input
                      type="number"
                      className="text-xs capitalize"
                      placeholder={unit}
                      value={Math.max(0, Number(field.value || 0))}
                      {...form.register(unit as DurationType, {
                        valueAsNumber: true,
                      })}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          ))}
        </div>
      )}

      {mode === "cron" && (
        <FormField
          control={form.control}
          name="cronExpression"
          render={({ field }) => (
            <FormItem>
              <FormLabel className="text-xs capitalize text-foreground/80">
                Cron Expression
              </FormLabel>
              <FormDescription className="text-xs">
                Standard 5 or 6 field cron format, e.g.{" "}
                <code className="font-mono">0 0 * * *</code>.
              </FormDescription>
              <FormControl>
                <Input
                  type="text"
                  className="text-xs font-mono"
                  placeholder="0 0 * * *"
                  {...field}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      )}

      <FormField
        key="timeout"
        control={form.control}
        name="timeout"
        render={({ field }) => (
          <FormItem>
            <FormLabel className="text-xs capitalize text-foreground/80">
              Timeout
            </FormLabel>
            <FormDescription className="text-xs">
              The maximum time in seconds the workflow can run for. Default is 0
              (no timeout).
            </FormDescription>
            <FormControl>
              <Input
                type="number"
                className="text-xs capitalize"
                placeholder="Timeout (seconds)"
                {...field}
                onChange={(e) =>
                  field.onChange(
                    e.target.value ? parseFloat(e.target.value) : undefined
                  )
                }
              />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />

      {mode === "interval" && (
        <FormField
          key="offset"
          control={form.control}
          name="offset"
          render={({ field }) => (
            <FormItem>
              <FormLabel className="text-xs capitalize text-foreground/80">
                Offset
              </FormLabel>
              <FormDescription className="text-xs">
                Optional delay before the first execution. Use ISO 8601 duration
                format: PT1H (1 hour), P1D (1 day), PT30M (30 minutes).
              </FormDescription>
              <FormControl>
                <Input
                  type="text"
                  className="text-xs"
                  placeholder="PT1H (optional)"
                  {...field}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      )}

      <FormField
        key="startAt"
        control={form.control}
        name="startAt"
        render={({ field }) => {
          const dateValue = field.value ? new Date(field.value) : undefined
          const timeValue = field.value
            ? new Date(field.value).toTimeString().slice(0, 8)
            : ""

          const handleDateChange = (date: Date | undefined) => {
            if (!date) {
              field.onChange("")
              setStartDatePickerOpen(false)
              return
            }

            const currentTime = field.value
              ? new Date(field.value).toTimeString().slice(0, 8)
              : "00:00:00"
            const [hours, minutes, seconds] = currentTime.split(":")
            date.setHours(
              Number.parseInt(hours),
              Number.parseInt(minutes),
              Number.parseInt(seconds)
            )
            field.onChange(date.toISOString())
            setStartDatePickerOpen(false)
          }

          const handleTimeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
            const timeStr = e.target.value
            const currentDate = field.value ? new Date(field.value) : new Date()

            const [hours, minutes, seconds] = timeStr.split(":")
            currentDate.setHours(
              Number.parseInt(hours || "0"),
              Number.parseInt(minutes || "0"),
              Number.parseInt(seconds || "0")
            )
            field.onChange(currentDate.toISOString())
          }

          return (
            <FormItem>
              <FormLabel className="text-xs capitalize text-foreground/80">
                Start after
              </FormLabel>
              <FormDescription className="text-xs">
                Optional earliest date and time to begin running this schedule.
              </FormDescription>
              <FormControl>
                <div className="flex gap-4">
                  <div className="flex flex-col gap-3">
                    <Popover
                      open={startDatePickerOpen}
                      onOpenChange={setStartDatePickerOpen}
                    >
                      <PopoverTrigger asChild>
                        <Button
                          variant="outline"
                          className="w-32 justify-between font-normal text-xs"
                        >
                          {dateValue
                            ? dateValue.toLocaleDateString()
                            : "Select date"}
                          <ChevronDownIcon className="size-4" />
                        </Button>
                      </PopoverTrigger>
                      <PopoverContent
                        className="w-auto overflow-hidden p-0"
                        align="start"
                        portal={true}
                      >
                        <Calendar
                          mode="single"
                          selected={dateValue}
                          captionLayout="dropdown"
                          onSelect={handleDateChange}
                        />
                      </PopoverContent>
                    </Popover>
                  </div>
                  <div className="flex flex-col gap-3">
                    <Input
                      type="time"
                      step="1"
                      value={timeValue}
                      onChange={handleTimeChange}
                      className="bg-background text-xs appearance-none [&::-webkit-calendar-picker-indicator]:hidden [&::-webkit-calendar-picker-indicator]:appearance-none"
                    />
                  </div>
                </div>
              </FormControl>
              <FormMessage />
            </FormItem>
          )
        }}
      />

      <FormField
        key="endAt"
        control={form.control}
        name="endAt"
        render={({ field }) => {
          const dateValue = field.value ? new Date(field.value) : undefined
          const timeValue = field.value
            ? new Date(field.value).toTimeString().slice(0, 8)
            : ""

          const handleDateChange = (date: Date | undefined) => {
            if (!date) {
              field.onChange("")
              setEndDatePickerOpen(false)
              return
            }

            const currentTime = field.value
              ? new Date(field.value).toTimeString().slice(0, 8)
              : "00:00:00"
            const [hours, minutes, seconds] = currentTime.split(":")
            date.setHours(
              Number.parseInt(hours),
              Number.parseInt(minutes),
              Number.parseInt(seconds)
            )
            field.onChange(date.toISOString())
            setEndDatePickerOpen(false)
          }

          const handleTimeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
            const timeStr = e.target.value
            const currentDate = field.value ? new Date(field.value) : new Date()

            const [hours, minutes, seconds] = timeStr.split(":")
            currentDate.setHours(
              Number.parseInt(hours || "0"),
              Number.parseInt(minutes || "0"),
              Number.parseInt(seconds || "0")
            )
            field.onChange(currentDate.toISOString())
          }

          return (
            <FormItem>
              <FormLabel className="text-xs capitalize text-foreground/80">
                End by
              </FormLabel>
              <FormDescription className="text-xs">
                Optional latest date and time after which the schedule will stop
                running.
              </FormDescription>
              <FormControl>
                <div className="flex gap-4">
                  <div className="flex flex-col gap-3">
                    <Popover
                      open={endDatePickerOpen}
                      onOpenChange={setEndDatePickerOpen}
                    >
                      <PopoverTrigger asChild>
                        <Button
                          variant="outline"
                          className="w-32 justify-between font-normal text-xs"
                        >
                          {dateValue
                            ? dateValue.toLocaleDateString()
                            : "Select date"}
                          <ChevronDownIcon className="size-4" />
                        </Button>
                      </PopoverTrigger>
                      <PopoverContent
                        className="w-auto overflow-hidden p-0"
                        align="start"
                        portal={true}
                      >
                        <Calendar
                          mode="single"
                          selected={dateValue}
                          captionLayout="dropdown"
                          onSelect={handleDateChange}
                        />
                      </PopoverContent>
                    </Popover>
                  </div>
                  <div className="flex flex-col gap-3">
                    <Input
                      type="time"
                      step="1"
                      value={timeValue}
                      onChange={handleTimeChange}
                      className="bg-background text-xs appearance-none [&::-webkit-calendar-picker-indicator]:hidden [&::-webkit-calendar-picker-indicator]:appearance-none"
                    />
                  </div>
                </div>
              </FormControl>
              <FormMessage />
            </FormItem>
          )
        }}
      />
    </div>
  )
}
