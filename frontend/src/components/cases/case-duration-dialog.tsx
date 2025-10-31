"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { FlagTriangleRight, Timer } from "lucide-react"
import { type ReactNode, useEffect, useMemo } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import {
  PRIORITIES,
  SEVERITIES,
  STATUSES,
} from "@/components/cases/case-categories"
import {
  CASE_DURATION_SELECTION_OPTIONS,
  CASE_EVENT_FILTER_OPTIONS,
  CASE_EVENT_OPTIONS,
  CASE_EVENT_VALUES,
  isCaseEventFilterType,
} from "@/components/cases/case-duration-options"
import {
  CaseFilterMultiSelect,
  type CaseFilterOption,
} from "@/components/cases/multiselect-case-filters"
import { Button } from "@/components/ui/button"
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"

const anchorSchema = z.object({
  selection: z.enum(["first", "last"]),
  eventType: z.enum(CASE_EVENT_VALUES),
  filterValues: z.array(z.string()).optional(),
})

const CATEGORY_OPTIONS = {
  priority_changed: PRIORITIES,
  severity_changed: SEVERITIES,
  status_changed: STATUSES,
} as const

const getOptionIconClass = (color?: string) =>
  color?.split(" ").find((token) => token.startsWith("text-")) ||
  "text-muted-foreground"

const formSchema = z
  .object({
    name: z
      .string()
      .min(1, "Name is required")
      .max(255, "Name must be 255 characters or fewer"),
    description: z
      .string()
      .max(1024, "Description must be 1024 characters or fewer")
      .optional(),
    start: anchorSchema,
    end: anchorSchema,
  })
  .superRefine((values, ctx) => {
    const startEventType = values.start.eventType
    const endEventType = values.end.eventType
    const startFilterValues = values.start.filterValues ?? []
    const endFilterValues = values.end.filterValues ?? []

    if (isCaseEventFilterType(startEventType)) {
      if (startFilterValues.length === 0) {
        const label = CASE_EVENT_FILTER_OPTIONS[startEventType].label
        ctx.addIssue({
          path: ["start", "filterValues"],
          code: z.ZodIssueCode.custom,
          message: `Select at least one ${label.toLowerCase()}.`,
        })
      }

      if (
        isCaseEventFilterType(endEventType) &&
        startEventType === endEventType
      ) {
        const overlappingValues = startFilterValues.filter((value) =>
          endFilterValues.includes(value)
        )
        if (overlappingValues.length > 0) {
          const label = CASE_EVENT_FILTER_OPTIONS[startEventType].label
          ctx.addIssue({
            path: ["start", "filterValues"],
            code: z.ZodIssueCode.custom,
            message: `Remove duplicate ${label.toLowerCase()} selections shared with the "To event".`,
          })
          ctx.addIssue({
            path: ["end", "filterValues"],
            code: z.ZodIssueCode.custom,
            message: `Choose ${label.toLowerCase()} values that differ from the "From event".`,
          })
        }
      }
    }

    if (isCaseEventFilterType(endEventType) && endFilterValues.length === 0) {
      const label = CASE_EVENT_FILTER_OPTIONS[endEventType].label
      ctx.addIssue({
        path: ["end", "filterValues"],
        code: z.ZodIssueCode.custom,
        message: `Select a ${label.toLowerCase()}.`,
      })
    }
  })

export type CaseDurationFormValues = z.infer<typeof formSchema>

const buildFilterOptions = (
  eventType: CaseDurationFormValues["start"]["eventType"]
): CaseFilterOption[] => {
  if (!isCaseEventFilterType(eventType)) {
    return []
  }

  const categoryMap = CATEGORY_OPTIONS[eventType] as Record<
    string,
    { icon: CaseFilterOption["icon"]; color?: string }
  >

  return CASE_EVENT_FILTER_OPTIONS[eventType].options.map((option) => {
    const category = categoryMap[option.value]
    return {
      value: option.value,
      label: option.label,
      icon: category?.icon,
      iconClassName: getOptionIconClass(category?.color),
    }
  })
}

export interface CaseDurationDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSubmit: (values: CaseDurationFormValues) => void | Promise<void>
  title: ReactNode
  description: ReactNode
  submitLabel: string
  isSubmitting?: boolean
  initialValues?: CaseDurationFormValues
}

export const createEmptyCaseDurationFormValues =
  (): CaseDurationFormValues => ({
    name: "",
    description: "",
    start: {
      selection: "first",
      eventType: "case_created",
      filterValues: [],
    },
    end: {
      selection: "first",
      eventType: "case_closed",
      filterValues: [],
    },
  })

export const buildFieldFilters = (
  eventType: CaseDurationFormValues["start"]["eventType"],
  filterValues: CaseDurationFormValues["start"]["filterValues"]
): Record<string, unknown> | null => {
  if (!isCaseEventFilterType(eventType)) {
    return null
  }

  if (filterValues && filterValues.length > 0) {
    return { "data.new": filterValues }
  }

  return null
}

export function CaseDurationDialog({
  open,
  onOpenChange,
  onSubmit,
  title,
  description,
  submitLabel,
  isSubmitting = false,
  initialValues,
}: CaseDurationDialogProps) {
  const form = useForm<CaseDurationFormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: initialValues ?? createEmptyCaseDurationFormValues(),
  })

  useEffect(() => {
    if (open) {
      form.reset(initialValues ?? createEmptyCaseDurationFormValues())
    }
  }, [open, initialValues, form])

  const startEventType = form.watch("start.eventType")
  const endEventType = form.watch("end.eventType")
  const nameValue = form.watch("name")
  const startFilterCount = form.watch("start.filterValues")?.length ?? 0
  const endFilterCount = form.watch("end.filterValues")?.length ?? 0

  const startFilterConfig =
    startEventType && isCaseEventFilterType(startEventType)
      ? CASE_EVENT_FILTER_OPTIONS[startEventType]
      : null
  const endFilterConfig =
    endEventType && isCaseEventFilterType(endEventType)
      ? CASE_EVENT_FILTER_OPTIONS[endEventType]
      : null
  const startFilterOptions = useMemo(
    () => buildFilterOptions(startEventType),
    [startEventType]
  )
  const endFilterOptions = useMemo(
    () => buildFilterOptions(endEventType),
    [endEventType]
  )
  const isSubmitDisabled = useMemo(() => {
    if (isSubmitting) {
      return true
    }
    const trimmedName = nameValue?.trim() ?? ""
    if (!trimmedName) {
      return true
    }
    if (!startEventType || !endEventType) {
      return true
    }
    if (isCaseEventFilterType(startEventType) && startFilterCount === 0) {
      return true
    }
    if (isCaseEventFilterType(endEventType) && endFilterCount === 0) {
      return true
    }
    return false
  }, [
    isSubmitting,
    nameValue,
    startEventType,
    endEventType,
    startFilterCount,
    endFilterCount,
  ])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[640px]">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Name</FormLabel>
                  <FormControl>
                    <Input placeholder="Time to resolution" {...field} />
                  </FormControl>
                  <FormDescription>
                    Choose a descriptive name for this duration metric.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="description"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Description</FormLabel>
                  <FormControl>
                    <Textarea
                      className="min-h-[100px] text-xs"
                      placeholder="Optional context for this metric"
                      {...field}
                    />
                  </FormControl>
                  <FormDescription>
                    Provide additional context for the team (optional).
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-4 rounded-md border bg-muted/20 p-4">
                <h4 className="flex items-center gap-2 text-sm font-medium">
                  <Timer className="size-3.5 text-muted-foreground" />
                  <span>From event</span>
                </h4>
                <FormField
                  control={form.control}
                  name="start.selection"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-xs">Occurrence</FormLabel>
                      <Select
                        value={field.value}
                        onValueChange={field.onChange}
                      >
                        <FormControl>
                          <SelectTrigger>
                            <SelectValue />
                          </SelectTrigger>
                        </FormControl>
                        <SelectContent>
                          {CASE_DURATION_SELECTION_OPTIONS.map((option) => (
                            <SelectItem key={option.value} value={option.value}>
                              {option.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="start.eventType"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-xs">Event type</FormLabel>
                      <Select
                        value={field.value}
                        onValueChange={(value) => {
                          form.setValue("start.filterValues", [])
                          field.onChange(value)
                        }}
                      >
                        <FormControl>
                          <SelectTrigger>
                            <SelectValue />
                          </SelectTrigger>
                        </FormControl>
                        <SelectContent>
                          {CASE_EVENT_OPTIONS.map((option) => (
                            <SelectItem key={option.value} value={option.value}>
                              <span className="flex items-center gap-2">
                                <option.icon className="size-3.5 text-muted-foreground" />
                                <span>{option.label}</span>
                              </span>
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                {startFilterConfig && (
                  <FormField
                    control={form.control}
                    name="start.filterValues"
                    render={({ field }) => (
                      <FormItem className="space-y-2">
                        <FormLabel className="text-xs">
                          {startFilterConfig.label}
                        </FormLabel>
                        <FormControl className="w-full">
                          <CaseFilterMultiSelect
                            placeholder={`Select ${startFilterConfig.label.toLowerCase()}`}
                            value={field.value ?? []}
                            options={startFilterOptions}
                            onChange={(nextValue) => {
                              field.onChange(nextValue)
                            }}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                )}
              </div>

              <div className="space-y-4 rounded-md border bg-muted/20 p-4">
                <h4 className="flex items-center gap-2 text-sm font-medium">
                  <FlagTriangleRight className="size-3.5 text-muted-foreground" />
                  <span>To event</span>
                </h4>
                <FormField
                  control={form.control}
                  name="end.selection"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-xs">Occurrence</FormLabel>
                      <Select
                        value={field.value}
                        onValueChange={field.onChange}
                      >
                        <FormControl>
                          <SelectTrigger>
                            <SelectValue />
                          </SelectTrigger>
                        </FormControl>
                        <SelectContent>
                          {CASE_DURATION_SELECTION_OPTIONS.map((option) => (
                            <SelectItem key={option.value} value={option.value}>
                              {option.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="end.eventType"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-xs">Event type</FormLabel>
                      <Select
                        value={field.value}
                        onValueChange={(value) => {
                          form.setValue("end.filterValues", [])
                          field.onChange(value)
                        }}
                      >
                        <FormControl>
                          <SelectTrigger>
                            <SelectValue />
                          </SelectTrigger>
                        </FormControl>
                        <SelectContent>
                          {CASE_EVENT_OPTIONS.map((option) => (
                            <SelectItem key={option.value} value={option.value}>
                              <span className="flex items-center gap-2">
                                <option.icon className="size-3.5 text-muted-foreground" />
                                <span>{option.label}</span>
                              </span>
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                {endFilterConfig && (
                  <FormField
                    control={form.control}
                    name="end.filterValues"
                    render={({ field }) => (
                      <FormItem className="space-y-2">
                        <FormLabel className="text-xs">
                          {endFilterConfig.label}
                        </FormLabel>
                        <FormControl className="w-full">
                          <CaseFilterMultiSelect
                            placeholder={`Select ${endFilterConfig.label.toLowerCase()}`}
                            value={field.value ?? []}
                            options={endFilterOptions}
                            onChange={(nextValue) => {
                              field.onChange(nextValue)
                            }}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                )}
              </div>
            </div>

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={isSubmitting}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={isSubmitDisabled}>
                {submitLabel}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

export { anchorSchema, formSchema }
