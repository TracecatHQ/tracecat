"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Plus, X } from "lucide-react"
import { useFieldArray, useForm } from "react-hook-form"
import { z } from "zod"
import {
  CASE_DURATION_SELECTION_OPTIONS,
  CASE_EVENT_OPTIONS,
  CASE_EVENT_VALUES,
} from "@/components/cases/case-duration-options"
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
import { toast } from "@/components/ui/use-toast"
import { createCaseDuration } from "@/lib/case-durations"
import { useWorkspaceId } from "@/providers/workspace-id"

const filterSchema = z.object({
  key: z.string().min(1, "Key is required"),
  value: z.string().min(1, "Value is required"),
})

const anchorSchema = z.object({
  selection: z.enum(["first", "last"]),
  eventType: z.enum(CASE_EVENT_VALUES),
  timestampPath: z
    .string()
    .min(1, "Timestamp path is required")
    .max(255, "Timestamp path must be 255 characters or fewer"),
  filters: z.array(filterSchema).default([]),
})

const formSchema = z.object({
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

type CaseDurationFormValues = z.infer<typeof formSchema>

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
        timestampPath: "created_at",
        filters: [],
      },
      end: {
        selection: "first",
        eventType: "case_closed",
        timestampPath: "created_at",
        filters: [],
      },
    },
  })

  const {
    fields: startFilterFields,
    append: appendStartFilter,
    remove: removeStartFilter,
  } = useFieldArray({ control: form.control, name: "start.filters" })

  const {
    fields: endFilterFields,
    append: appendEndFilter,
    remove: removeEndFilter,
  } = useFieldArray({ control: form.control, name: "end.filters" })

  const { mutateAsync: handleCreate, isPending } = useMutation({
    mutationFn: async (values: CaseDurationFormValues) => {
      if (!workspaceId) {
        throw new Error("Workspace ID is required")
      }

      const transformFilters = (
        filters: CaseDurationFormValues["start"]["filters"]
      ) =>
        Object.fromEntries(filters.map((filter) => [filter.key, filter.value]))

      const payload = {
        name: values.name.trim(),
        description: values.description?.trim() || null,
        start_anchor: {
          event_type: values.start.eventType,
          selection: values.start.selection,
          timestamp_path: values.start.timestampPath.trim(),
          field_filters: transformFilters(values.start.filters),
        },
        end_anchor: {
          event_type: values.end.eventType,
          selection: values.end.selection,
          timestamp_path: values.end.timestampPath.trim(),
          field_filters: transformFilters(values.end.filters),
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

  const renderFilters = (
    type: "start" | "end",
    fields: typeof startFilterFields,
    remove: (index: number) => void,
    append: () => void
  ) => {
    const fieldName = `${type}.filters` as const

    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <FormLabel className="text-xs font-medium text-muted-foreground">
            Filters (optional)
          </FormLabel>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={() => append()}
          >
            <Plus className="mr-1 size-3.5" />
            Add filter
          </Button>
        </div>
        {fields.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            Add key/value filters to match specific event payloads.
          </p>
        ) : (
          <div className="space-y-2">
            {fields.map((field, index) => (
              <div key={field.id} className="flex items-start gap-2">
                <FormField
                  control={form.control}
                  name={`${fieldName}.${index}.key`}
                  render={({ field }) => (
                    <FormItem className="flex-1">
                      <FormControl>
                        <Input placeholder="payload.path" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name={`${fieldName}.${index}.value`}
                  render={({ field }) => (
                    <FormItem className="flex-1">
                      <FormControl>
                        <Input placeholder="expected value" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="mt-0.5 text-muted-foreground"
                  onClick={() => remove(index)}
                >
                  <X className="size-3.5" />
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>
    )
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
                <h4 className="text-sm font-medium">From event</h4>
                <FormField
                  control={form.control}
                  name="start.selection"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Occurrence</FormLabel>
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
                      <FormLabel>Event type</FormLabel>
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
                <FormField
                  control={form.control}
                  name="start.timestampPath"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Timestamp field</FormLabel>
                      <FormControl>
                        <Input placeholder="created_at" {...field} />
                      </FormControl>
                      <FormDescription>
                        Path to the timestamp on the event payload.
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                {renderFilters(
                  "start",
                  startFilterFields,
                  removeStartFilter,
                  () => appendStartFilter({ key: "", value: "" })
                )}
              </div>

              <div className="space-y-4 rounded-md border bg-muted/20 p-4">
                <h4 className="text-sm font-medium">To event</h4>
                <FormField
                  control={form.control}
                  name="end.selection"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Occurrence</FormLabel>
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
                      <FormLabel>Event type</FormLabel>
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
                <FormField
                  control={form.control}
                  name="end.timestampPath"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Timestamp field</FormLabel>
                      <FormControl>
                        <Input placeholder="created_at" {...field} />
                      </FormControl>
                      <FormDescription>
                        Path to the timestamp on the event payload.
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                {renderFilters("end", endFilterFields, removeEndFilter, () =>
                  appendEndFilter({ key: "", value: "" })
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
