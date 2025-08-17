"use client"

import { Database, Loader2, Plus } from "lucide-react"
import { useMemo, useState } from "react"
import type { CaseEntityRead, CaseRecordLinkRead } from "@/client"
import { CreateEntityRecordDialog } from "@/components/cases/create-entity-record-dialog"
import { EditEntityRecordDialog } from "@/components/cases/edit-entity-record-dialog"
import { EntityRecordCard } from "@/components/cases/entity-record-card"
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
import { Skeleton } from "@/components/ui/skeleton"
import {
  useDeleteCaseRecord,
  useListCaseRecords,
  useRemoveCaseRecordLink,
} from "@/lib/hooks"
import { cn } from "@/lib/utils"

interface CaseEntitiesSectionProps {
  caseId: string
  workspaceId: string
}

export function CaseEntitiesSection({
  caseId,
  workspaceId,
}: CaseEntitiesSectionProps) {
  const [showCreateDialog, setShowCreateDialog] = useState(false)
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

  // Build unique entities list from recordLinks for relation tooltips
  const uniqueEntities = useMemo(() => {
    if (!records) return []
    const entitiesMap = new Map<string, CaseEntityRead>()
    records.forEach((link) => {
      if (link.entity) {
        entitiesMap.set(link.entity.id, link.entity)
      }
    })
    return Array.from(entitiesMap.values())
  }, [records])

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

  // Group records by entity type for better organization (optional)
  const recordsByEntity = records?.reduce(
    (acc, record) => {
      const entityId = record.record?.entity_id || record.entity_id
      if (!acc[entityId]) {
        acc[entityId] = []
      }
      acc[entityId].push(record)
      return acc
    },
    {} as Record<string, CaseRecordLinkRead[]>
  )

  return (
    <div className="space-y-4 p-4">
      {/* Add entity record button */}
      <div
        onClick={() => setShowCreateDialog(true)}
        className={cn(
          "flex items-center gap-2 p-1.5 rounded-md border border-dashed transition-all cursor-pointer group",
          "border-muted-foreground/25 hover:border-muted-foreground/50 hover:bg-muted/30"
        )}
      >
        <div className="p-1.5 rounded bg-muted group-hover:bg-muted-foreground/10 transition-colors">
          <Plus className="h-3.5 w-3.5 text-muted-foreground" />
        </div>
        <span className="text-xs text-muted-foreground group-hover:text-foreground transition-colors">
          Add entity record
        </span>
      </div>

      {/* Records list */}
      {isLoadingRecords ? (
        <div className="space-y-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : !records || records.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-4">
          <div className="p-2 rounded-full bg-muted/50 mb-3">
            <Database className="h-5 w-5 text-muted-foreground" />
          </div>
          <h3 className="text-sm font-medium text-muted-foreground mb-1">
            No entities found
          </h3>
          <p className="text-xs text-muted-foreground/75 text-center max-w-[250px]">
            Add entity records to track structured data for this case
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {recordsByEntity &&
            Object.entries(recordsByEntity).map(([entityId, entityRecords]) => {
              // Use the entity from the first record link (they all have the same entity)
              const entity = entityRecords[0]?.entity
              return (
                <div key={entityId} className="space-y-3">
                  {entity && entityRecords.length > 1 && (
                    <h3 className="text-xs font-medium text-muted-foreground">
                      {entity.display_name} ({entityRecords.length})
                    </h3>
                  )}
                  {entityRecords.map((recordLink) => (
                    <EntityRecordCard
                      key={recordLink.id}
                      recordLink={recordLink}
                      entity={recordLink.entity}
                      entities={uniqueEntities}
                      workspaceId={workspaceId}
                      onEdit={() => setEditingRecord(recordLink)}
                      onDelete={() => setDeletingRecord(recordLink)}
                      onRemoveLink={() => setRemovingLink(recordLink)}
                    />
                  ))}
                </div>
              )
            })}
        </div>
      )}

      {/* Create dialog */}
      <CreateEntityRecordDialog
        open={showCreateDialog}
        onOpenChange={setShowCreateDialog}
        caseId={caseId}
        workspaceId={workspaceId}
        onSuccess={() => setShowCreateDialog(false)}
      />

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
            <AlertDialogTitle>Delete entity record?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently delete this entity record. This action
              cannot be undone.
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
