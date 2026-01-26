"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { DatabaseIcon } from "lucide-react"
import { casesDeleteField } from "@/client"
import { CustomFieldsTable } from "@/components/cases/custom-fields-table"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { toast } from "@/components/ui/use-toast"
import { useWorkspaceDetails } from "@/hooks/use-workspace"
import { useCaseFields } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export function CustomFieldsView() {
  const workspaceId = useWorkspaceId()
  const { workspace, workspaceLoading, workspaceError } = useWorkspaceDetails()
  const queryClient = useQueryClient()

  const { caseFields, caseFieldsIsLoading, caseFieldsError } =
    useCaseFields(workspaceId)

  const { mutateAsync: deleteCaseField, isPending: deleteCaseFieldIsPending } =
    useMutation({
      mutationFn: async (fieldId: string) => {
        return await casesDeleteField({
          workspaceId,
          fieldId,
        })
      },
      onSuccess: () => {
        queryClient.invalidateQueries({
          queryKey: ["case-fields", workspaceId],
        })
        toast({
          title: "Field deleted",
          description: "The case field was deleted successfully.",
        })
      },
      onError: (error) => {
        console.error("Failed to delete case field", error)
        toast({
          title: "Error deleting field",
          description: "Failed to delete the case field. Please try again.",
          variant: "destructive",
        })
      },
    })

  const handleDeleteField = async (fieldId: string) => {
    // Ensure caseFields exists before attempting to find a field
    if (!caseFields) {
      return
    }

    // Find the field to check if it's reserved
    const field = caseFields.find((f) => f.id === fieldId)

    // Don't allow deletion of reserved fields
    if (field && field.reserved) {
      return
    }

    await deleteCaseField(fieldId)
  }

  if (workspaceLoading || caseFieldsIsLoading) {
    return <CenteredSpinner />
  }

  if (workspaceError) {
    return (
      <AlertNotification
        level="error"
        message="Error loading workspace info."
      />
    )
  }

  if (!workspace) {
    return <AlertNotification level="error" message="Workspace not found." />
  }

  if (caseFieldsError || !caseFields) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading case fields: ${caseFieldsError?.message || "Unknown error"}`}
      />
    )
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-8 py-8">
        {caseFields.filter((field) => !field.reserved).length === 0 ? (
          <Empty className="h-full">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <DatabaseIcon className="size-6" />
              </EmptyMedia>
              <EmptyTitle>No custom fields defined yet</EmptyTitle>
              <EmptyDescription>
                Add your first custom field using the button in the header
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <div className="space-y-4">
            <CustomFieldsTable
              fields={caseFields}
              onDeleteField={handleDeleteField}
              isDeleting={deleteCaseFieldIsPending}
            />
          </div>
        )}
      </div>
    </div>
  )
}
