"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { FlagTriangleRight, Tag, Timer } from "lucide-react"
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
  isCaseTagEventType,
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
import { useCaseTagCatalog } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

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

type CaseDurationEventTypeValue = (typeof CASE_EVENT_VALUES)[number]

const requiresFilterSelection = (
  eventType: CaseDurationEventTypeValue
): boolean => {
  return isCaseEventFilterType(eventType) || isCaseTagEventType(eventType)
}

const getFilterLabel = (
  eventType: CaseDurationEventTypeValue
): string | null => {
  if (isCaseEventFilterType(eventType)) {
    return CASE_EVENT_FILTER_OPTIONS[eventType].label
  }
  if (isCaseTagEventType(eventType)) {
    return "Tag"
  }
  return null
}

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

    const startRequiresFilter = requiresFilterSelection(startEventType)
    const endRequiresFilter = requiresFilterSelection(endEventType)

    if (startRequiresFilter && startFilterValues.length === 0) {
      const label = getFilterLabel(startEventType) ?? "value"
      ctx.addIssue({
        path: ["start", "filterValues"],
        code: z.ZodIssueCode.custom,
        message: `Select at least one ${label.toLowerCase()}.`,
      })
    }

    if (endRequiresFilter && endFilterValues.length === 0) {
      const label = getFilterLabel(endEventType) ?? "value"
      ctx.addIssue({
        path: ["end", "filterValues"],
        code: z.ZodIssueCode.custom,
        message: `Select a ${label.toLowerCase()}.`,
      })
    }

    if (
      startRequiresFilter &&
      endRequiresFilter &&
      startEventType === endEventType
    ) {
      const overlappingValues = startFilterValues.filter((value) =>
        endFilterValues.includes(value)
      )
      if (overlappingValues.length > 0) {
        const label = getFilterLabel(startEventType) ?? "value"
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
  })

export type CaseDurationFormValues = z.infer<typeof formSchema>

const buildFilterOptions = (
  eventType: CaseDurationFormValues["start"]["eventType"] | undefined,
  tagOptions: CaseFilterOption[]
): CaseFilterOption[] => {
  if (!eventType) {
    return []
  }

  if (isCaseEventFilterType(eventType)) {
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

  if (isCaseTagEventType(eventType)) {
    return tagOptions
  }

  return []
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

export const normalizeFilterValues = (value: unknown): string[] => {
  if (Array.isArray(value)) {
    return value.filter((item): item is string => typeof item === "string")
  }

  if (typeof value === "string") {
    return [value]
  }

  if (
    value &&
    typeof value === "object" &&
    Array.isArray((value as { $in?: unknown[] }).$in)
  ) {
    const inArray = (value as { $in: unknown[] }).$in
    return inArray.filter((item): item is string => typeof item === "string")
  }

  return []
}

export const getFilterFieldKey = (
  eventType: CaseDurationFormValues["start"]["eventType"]
): string | null => {
  if (isCaseEventFilterType(eventType)) {
    return "data.new"
  }
  if (isCaseTagEventType(eventType)) {
    return "data.tag_ref"
  }
  return null
}

export const buildFieldFilters = (
  eventType: CaseDurationFormValues["start"]["eventType"],
  filterValues: CaseDurationFormValues["start"]["filterValues"]
): Record<string, unknown> | null => {
  const fieldKey = getFilterFieldKey(eventType)
  if (!fieldKey || !filterValues || filterValues.length === 0) {
    return null
  }

  return { [fieldKey]: filterValues }
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

  const workspaceId = useWorkspaceId()
  const { caseTags } = useCaseTagCatalog(workspaceId ?? "", {
    enabled: Boolean(workspaceId),
  })

  const tagFilterOptions = useMemo<CaseFilterOption[]>(() => {
    if (!caseTags) {
      return []
    }

    return caseTags.map((tag) => ({
      value: tag.ref,
      label: tag.name,
      icon: Tag,
    }))
  }, [caseTags])

  const startEventType = form.watch("start.eventType")
  const endEventType = form.watch("end.eventType")
  const nameValue = form.watch("name")
  const startFilterCount = form.watch("start.filterValues")?.length ?? 0
  const endFilterCount = form.watch("end.filterValues")?.length ?? 0

  const startFilterOptions = useMemo(
    () => buildFilterOptions(startEventType, tagFilterOptions),
    [startEventType, tagFilterOptions]
  )
  const endFilterOptions = useMemo(
    () => buildFilterOptions(endEventType, tagFilterOptions),
    [endEventType, tagFilterOptions]
  )

  const startFilterLabel = startEventType
    ? getFilterLabel(startEventType)
    : null
  const endFilterLabel = endEventType ? getFilterLabel(endEventType) : null
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
    if (requiresFilterSelection(startEventType) && startFilterCount === 0) {
      return true
    }
    if (requiresFilterSelection(endEventType) && endFilterCount === 0) {
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
                {startFilterLabel && (
                  <FormField
                    control={form.control}
                    name="start.filterValues"
                    render={({ field }) => (
                      <FormItem className="space-y-2">
                        <FormLabel className="text-xs">
                          {startFilterLabel}
                        </FormLabel>
                        <FormControl className="w-full">
                          <CaseFilterMultiSelect
                            placeholder={`Select ${startFilterLabel.toLowerCase()}`}
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
                {endFilterLabel && (
                  <FormField
                    control={form.control}
                    name="end.filterValues"
                    render={({ field }) => (
                      <FormItem className="space-y-2">
                        <FormLabel className="text-xs">
                          {endFilterLabel}
                        </FormLabel>
                        <FormControl className="w-full">
                          <CaseFilterMultiSelect
                            placeholder={`Select ${endFilterLabel.toLowerCase()}`}
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
