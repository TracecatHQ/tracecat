"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Check, ChevronsUpDown, FlagTriangleRight, Timer } from "lucide-react"
import { type ComponentType, useMemo, useState } from "react"
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
  type CaseEventFilterOption,
  isCaseEventFilterType,
} from "@/components/cases/case-duration-options"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
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
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"
import { createCaseDuration } from "@/lib/case-durations"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

const anchorSchema = z.object({
  selection: z.enum(["first", "last"]),
  eventType: z.enum(CASE_EVENT_VALUES),
  filterValue: z.string().optional(),
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
    const endFilterValue = values.end.filterValue

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
        startEventType === endEventType &&
        endFilterValue &&
        startFilterValues.includes(endFilterValue)
      ) {
        const label = CASE_EVENT_FILTER_OPTIONS[startEventType].label
        ctx.addIssue({
          path: ["start", "filterValues"],
          code: z.ZodIssueCode.custom,
          message: `The selected ${label.toLowerCase()} already appears in the "From event" list.`,
        })
        ctx.addIssue({
          path: ["end", "filterValue"],
          code: z.ZodIssueCode.custom,
          message: `Choose a different ${label.toLowerCase()} for the "To event".`,
        })
      }
    }

    if (isCaseEventFilterType(endEventType) && !endFilterValue) {
      const label = CASE_EVENT_FILTER_OPTIONS[endEventType].label
      ctx.addIssue({
        path: ["end", "filterValue"],
        code: z.ZodIssueCode.custom,
        message: `Select a ${label.toLowerCase()}.`,
      })
    }
  })

type CaseDurationFormValues = z.infer<typeof formSchema>

const getFilterOptionMeta = (
  eventType: CaseDurationFormValues["start"]["eventType"],
  optionValue: string
) => {
  if (!isCaseEventFilterType(eventType)) {
    return undefined
  }

  const categoryMap = CATEGORY_OPTIONS[eventType] as Record<
    string,
    { icon?: ComponentType<{ className?: string }>; color?: string }
  >

  const category = categoryMap[optionValue]
  if (!category) {
    return undefined
  }

  return {
    icon: category.icon,
    iconClassName: getOptionIconClass(category.color),
  }
}

interface CaseEventFilterMultiSelectProps {
  placeholder: string
  value: string[]
  options: CaseEventFilterOption[]
  onChange: (value: string[]) => void
  emptyMessage?: string
  className?: string
  getOptionMeta?: (value: string) =>
    | {
        icon?: ComponentType<{ className?: string }>
        iconClassName?: string
      }
    | undefined
}

function CaseEventFilterMultiSelect({
  placeholder,
  value,
  options,
  onChange,
  emptyMessage = "No results found.",
  className,
  getOptionMeta,
}: CaseEventFilterMultiSelectProps) {
  const [open, setOpen] = useState(false)

  const valueSet = useMemo(() => new Set(value), [value])
  const optionMap = useMemo(() => {
    const map = new Map<string, CaseEventFilterOption>()
    for (const option of options) {
      map.set(option.value, option)
    }
    return map
  }, [options])

  const selectedCount = value.length
  const searchLabel =
    placeholder
      .replace(/^Select\s+/i, "")
      .trim()
      .toLowerCase() || "values"
  const triggerLabel =
    selectedCount === 0
      ? placeholder
      : selectedCount === 1
        ? (optionMap.get(value[0])?.label ?? placeholder)
        : `${placeholder} (${selectedCount})`

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          role="combobox"
          className={cn("h-8 w-full justify-between px-3 text-xs", className)}
        >
          <span className="truncate text-left">{triggerLabel}</span>
          <ChevronsUpDown className="ml-2 size-3 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className="w-[220px] p-0 sm:w-[260px]"
        align="start"
        side="top"
        sideOffset={6}
        avoidCollisions={false}
        style={{ width: "var(--radix-popover-trigger-width)" }}
      >
        <Command>
          <CommandInput
            placeholder={`Search ${searchLabel}...`}
            className="text-xs"
          />
          <CommandList>
            <CommandEmpty>{emptyMessage}</CommandEmpty>
            <CommandGroup>
              {options.map((option) => {
                const isSelected = valueSet.has(option.value)
                const meta = getOptionMeta?.(option.value)
                const Icon = meta?.icon
                return (
                  <CommandItem
                    key={option.value}
                    value={`${option.label} ${option.value}`}
                    className="flex items-center gap-2 text-xs"
                    onSelect={() => {
                      const nextValue = isSelected
                        ? value.filter((item) => item !== option.value)
                        : [...value, option.value]
                      onChange(nextValue)
                      setOpen(true)
                    }}
                  >
                    <div
                      className={cn(
                        "mr-2 flex size-4 items-center justify-center rounded-sm border",
                        isSelected
                          ? "border-primary bg-primary text-primary-foreground"
                          : "border-muted text-muted-foreground"
                      )}
                    >
                      <Check
                        className={cn("size-3", !isSelected && "opacity-0")}
                      />
                    </div>
                    {Icon ? (
                      <Icon
                        className={cn(
                          "size-3.5 text-muted-foreground",
                          meta?.iconClassName
                        )}
                      />
                    ) : null}
                    <span className="truncate">{option.label}</span>
                  </CommandItem>
                )
              })}
            </CommandGroup>
          </CommandList>
        </Command>
        {selectedCount > 0 && (
          <div className="border-t p-1">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-full text-xs"
              onClick={() => onChange([])}
            >
              Clear selection
            </Button>
          </div>
        )}
      </PopoverContent>
    </Popover>
  )
}

interface AddCaseDurationDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function AddCaseDurationDialog({
  open,
  onOpenChange,
}: AddCaseDurationDialogProps) {
  const workspaceId = useWorkspaceId()
  const queryClient = useQueryClient()

  const form = useForm<CaseDurationFormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      name: "",
      description: "",
      start: {
        selection: "first",
        eventType: "case_created",
        filterValue: undefined,
        filterValues: [],
      },
      end: {
        selection: "first",
        eventType: "case_closed",
        filterValue: undefined,
        filterValues: [],
      },
    },
  })

  const buildFieldFilters = (
    eventType: CaseDurationFormValues["start"]["eventType"],
    filterValue: CaseDurationFormValues["start"]["filterValue"],
    filterValues: CaseDurationFormValues["start"]["filterValues"]
  ) => {
    if (!isCaseEventFilterType(eventType)) {
      return null
    }

    if (filterValues && filterValues.length > 0) {
      return { "data.new": filterValues }
    }

    if (filterValue) {
      return { "data.new": filterValue }
    }

    return null
  }

  const startEventType = form.watch("start.eventType")
  const endEventType = form.watch("end.eventType")

  const startFilterConfig =
    startEventType && isCaseEventFilterType(startEventType)
      ? CASE_EVENT_FILTER_OPTIONS[startEventType]
      : null
  const endFilterConfig =
    endEventType && isCaseEventFilterType(endEventType)
      ? CASE_EVENT_FILTER_OPTIONS[endEventType]
      : null

  const { mutateAsync: handleCreate, isPending } = useMutation({
    mutationFn: async (values: CaseDurationFormValues) => {
      if (!workspaceId) {
        throw new Error("Workspace ID is required")
      }

      const startFieldFilters = buildFieldFilters(
        values.start.eventType,
        values.start.filterValue,
        values.start.filterValues
      )
      const endFieldFilters = buildFieldFilters(
        values.end.eventType,
        values.end.filterValue,
        values.end.filterValues
      )

      const payload = {
        name: values.name.trim(),
        description: values.description?.trim() || null,
        start_anchor: {
          event_type: values.start.eventType,
          selection: values.start.selection,
          timestamp_path: "created_at",
          ...(startFieldFilters ? { field_filters: startFieldFilters } : {}),
        },
        end_anchor: {
          event_type: values.end.eventType,
          selection: values.end.selection,
          timestamp_path: "created_at",
          ...(endFieldFilters ? { field_filters: endFieldFilters } : {}),
        },
      }

      await createCaseDuration(workspaceId, payload)
    },
    onSuccess: async () => {
      if (!workspaceId) {
        return
      }

      await queryClient.invalidateQueries({
        queryKey: ["case-durations", workspaceId],
      })

      toast({
        title: "Duration created",
        description: "The case duration was added successfully.",
      })

      form.reset()
      onOpenChange(false)
    },
    onError: (error: unknown) => {
      console.error("Failed to create case duration", error)
      toast({
        title: "Error creating duration",
        description:
          error instanceof Error
            ? error.message
            : "Failed to create the case duration. Please try again.",
        variant: "destructive",
      })
    },
  })

  const onSubmit = (values: CaseDurationFormValues) => {
    void handleCreate(values)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[640px]">
        <DialogHeader>
          <DialogTitle>Add duration</DialogTitle>
          <DialogDescription>
            Define a duration metric using matching case events.
          </DialogDescription>
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
                          form.setValue("start.filterValue", undefined)
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
                          <CaseEventFilterMultiSelect
                            placeholder={`Select ${startFilterConfig.label.toLowerCase()}`}
                            value={field.value ?? []}
                            options={startFilterConfig.options}
                            onChange={(nextValue) => {
                              field.onChange(nextValue)
                              void form.trigger([
                                "start.filterValues",
                                "end.filterValue",
                              ])
                            }}
                            getOptionMeta={(optionValue) =>
                              getFilterOptionMeta(startEventType, optionValue)
                            }
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
                          form.setValue("end.filterValue", undefined)
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
                    name="end.filterValue"
                    render={({ field }) => (
                      <FormItem className="space-y-2">
                        <FormLabel className="text-xs">
                          {endFilterConfig.label}
                        </FormLabel>
                        <Select
                          value={field.value}
                          onValueChange={(value) => {
                            field.onChange(value)
                            void form.trigger([
                              "start.filterValues",
                              "end.filterValue",
                            ])
                          }}
                        >
                          <FormControl className="w-full">
                            <SelectTrigger>
                              <SelectValue
                                placeholder={`Select ${endFilterConfig.label.toLowerCase()}`}
                              />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            {endFilterConfig.options.map((option) => (
                              <SelectItem
                                key={option.value}
                                value={option.value}
                              >
                                {option.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
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
              >
                Cancel
              </Button>
              <Button type="submit" disabled={isPending}>
                {isPending ? "Creating..." : "Create duration"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
