"use client"

import { Loader2 } from "lucide-react"
import { useState } from "react"
import type { CaseRecordLinkRead } from "@/client"
import { CreateEntityRecordDialog } from "@/components/cases/create-entity-record-dialog"
import { EditEntityRecordDialog } from "@/components/cases/edit-entity-record-dialog"
import { EntityRecordsTable } from "@/components/cases/entity-records-table"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import {
  useDeleteCaseRecord,
  useListCaseRecords,
  useListEntities,
  useRemoveCaseRecordLink,
} from "@/lib/hooks"

interface CaseEntitiesSectionProps {
  caseId: string
  workspaceId: string
}

export function CaseEntitiesSection({
  caseId,
  workspaceId,
}: CaseEntitiesSectionProps) {
  const [selectedEntityId, setSelectedEntityId] = useState<string | null>(null)
  const [editingRecord, setEditingRecord] = useState<CaseRecordLinkRead | null>(
    null
  )
  const [deletingRecord, setDeletingRecord] =
    useState<CaseRecordLinkRead | null>(null)
  const [removingLink, setRemovingLink] = useState<CaseRecordLinkRead | null>(
    null
  )

  const { records, isLoading: isLoadingRecords } = useListCaseRecords({
    caseId,
    workspaceId,
  })

  const { entities, isLoading: isLoadingEntities } = useListEntities({
    workspaceId,
    includeInactive: false,
  })

  const { deleteRecord, isDeleting } = useDeleteCaseRecord({
    caseId,
    workspaceId,
  })

  const { removeLink, isRemoving } = useRemoveCaseRecordLink({
    caseId,
    workspaceId,
  })

  const handleDelete = async () => {
    if (!deletingRecord) return
    try {
      await deleteRecord(deletingRecord.record?.id || deletingRecord.record_id)
      setDeletingRecord(null)
    } catch (error) {
      console.error("Failed to delete record:", error)
    }
  }

  const handleRemoveLink = async () => {
    if (!removingLink) return
    try {
      await removeLink(removingLink.id)
      setRemovingLink(null)
    } catch (error) {
      console.error("Failed to remove link:", error)
    }
  }

  return (
    <div className="space-y-4 p-4">
      {/* Records table */}
      <EntityRecordsTable
        records={records || []}
        isLoading={isLoadingRecords}
        onEdit={(recordLink) => setEditingRecord(recordLink)}
        onDelete={(recordLink) => setDeletingRecord(recordLink)}
        onRemoveLink={(recordLink) => setRemovingLink(recordLink)}
        onAddEntity={(entityId) => setSelectedEntityId(entityId)}
        entities={entities}
        isLoadingEntities={isLoadingEntities}
      />

      {/* Create dialog */}
      {selectedEntityId && (
        <CreateEntityRecordDialog
          open={!!selectedEntityId}
          onOpenChange={(open) => {
            if (!open) {
              setSelectedEntityId(null)
            }
          }}
          entityId={selectedEntityId}
          caseId={caseId}
          workspaceId={workspaceId}
          onSuccess={() => setSelectedEntityId(null)}
        />
      )}

      {/* Edit dialog */}
      {editingRecord && (
        <EditEntityRecordDialog
          open={!!editingRecord}
          onOpenChange={(open) => !open && setEditingRecord(null)}
          caseId={caseId}
          recordLink={editingRecord}
          workspaceId={workspaceId}
          onSuccess={() => setEditingRecord(null)}
        />
      )}

      {/* Delete confirmation dialog */}
      <AlertDialog
        open={!!deletingRecord}
        onOpenChange={(open) => !open && setDeletingRecord(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete record?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently delete this record. This action cannot be
              undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={isDeleting}
            >
              {isDeleting ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Deleting...
                </>
              ) : (
                "Delete"
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Remove link confirmation dialog */}
      <AlertDialog
        open={!!removingLink}
        onOpenChange={(open) => !open && setRemovingLink(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove entity record link?</AlertDialogTitle>
            <AlertDialogDescription>
              This will remove the link between this entity record and the case.
              The entity record itself will not be deleted.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleRemoveLink} disabled={isRemoving}>
              {isRemoving ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Removing...
                </>
              ) : (
                "Remove link"
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
