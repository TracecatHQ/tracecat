"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { DatabaseIcon } from "lucide-react"
import { useState } from "react"
import { type CaseFieldReadMinimal, casesDeleteField } from "@/client"
import { CustomFieldsTable } from "@/components/cases/custom-fields-table"
import { EditCustomFieldDialog } from "@/components/cases/edit-custom-field-dialog"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { toast } from "@/components/ui/use-toast"
import { useWorkspaceDetails } from "@/hooks/use-workspace"
import { useCaseFields } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export function CustomFieldsView() {
  const workspaceId = useWorkspaceId()
  const { workspace, workspaceLoading, workspaceError } = useWorkspaceDetails()
  const queryClient = useQueryClient()

  const [editDialogOpen, setEditDialogOpen] = useState(false)
  const [fieldToEdit, setFieldToEdit] = useState<CaseFieldReadMinimal | null>(
    null
  )

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

  const handleEditField = (field: CaseFieldReadMinimal) => {
    setFieldToEdit(field)
    setEditDialogOpen(true)
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
          <div className="flex h-full flex-col items-center justify-center gap-4">
            <div className="rounded-full bg-muted p-3">
              <DatabaseIcon className="size-8 text-muted-foreground" />
            </div>
            <div className="space-y-1 text-center">
              <h4 className="text-sm font-semibold text-muted-foreground">
                No custom fields defined yet
              </h4>
              <p className="text-xs text-muted-foreground">
                Add your first custom field using the button in the header
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <CustomFieldsTable
              fields={caseFields}
              onDeleteField={handleDeleteField}
              onEditField={handleEditField}
              isDeleting={deleteCaseFieldIsPending}
            />
          </div>
        )}
      </div>
      <EditCustomFieldDialog
        open={editDialogOpen}
        onOpenChange={setEditDialogOpen}
        field={fieldToEdit}
      />
    </div>
  )
}
