"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import {
  buildFieldFilters,
  CaseDurationDialog,
  type CaseDurationFormValues,
} from "@/components/cases/case-duration-dialog"
import { toast } from "@/components/ui/use-toast"
import { createCaseDurationDefinition } from "@/lib/case-durations"
import { useWorkspaceId } from "@/providers/workspace-id"

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

  const { mutateAsync: handleCreate, isPending } = useMutation({
    mutationFn: async (values: CaseDurationFormValues) => {
      if (!workspaceId) {
        throw new Error("Workspace ID is required")
      }

      const startFieldFilters = buildFieldFilters(
        values.start.eventType,
        values.start.filterValues
      )
      const endFieldFilters = buildFieldFilters(
        values.end.eventType,
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

      await createCaseDurationDefinition(workspaceId, payload)
    },
    onSuccess: async () => {
      if (!workspaceId) {
        return
      }

      await queryClient.invalidateQueries({
        queryKey: ["case-duration-definitions", workspaceId],
      })

      toast({
        title: "Duration created",
        description: "The case duration definition was added successfully.",
      })

      onOpenChange(false)
    },
    onError: (error: unknown) => {
      console.error("Failed to create case duration definition", error)
      toast({
        title: "Error creating duration",
        description:
          error instanceof Error
            ? error.message
            : "Failed to create the case duration definition. Please try again.",
        variant: "destructive",
      })
    },
  })

  const onSubmit = (values: CaseDurationFormValues) => {
    void handleCreate(values)
  }

  return (
    <CaseDurationDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Add duration"
      description="Define a duration metric using matching case events."
      submitLabel={isPending ? "Creating..." : "Create duration"}
      isSubmitting={isPending}
      onSubmit={onSubmit}
    />
  )
}
