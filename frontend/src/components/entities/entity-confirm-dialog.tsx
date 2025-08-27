"use client"

import type { EntityRead } from "@/client"
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

interface BaseProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  isPending?: boolean
}

interface EntityActionDialogProps extends BaseProps {
  selectedEntity: EntityRead | null
  setSelectedEntity: (entity: EntityRead | null) => void
  onConfirm: (entityId: string) => Promise<void>
}

export function EntityArchiveAlertDialog({
  open,
  onOpenChange,
  selectedEntity,
  setSelectedEntity,
  onConfirm,
  isPending,
}: EntityActionDialogProps) {
  return (
    <AlertDialog
      open={open}
      onOpenChange={(isOpen) => {
        if (!isOpen) setSelectedEntity(null)
        onOpenChange(isOpen)
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Archive entity</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to archive the entity{" "}
            <strong>{selectedEntity?.display_name}</strong>? This will hide the
            entity from normal use, but all data will be preserved. You can
            restore the entity later.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isPending}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={async () => {
              if (!selectedEntity) return
              await onConfirm(selectedEntity.id)
              setSelectedEntity(null)
            }}
            disabled={isPending}
          >
            Archive
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

export function EntityDeleteAlertDialog({
  open,
  onOpenChange,
  selectedEntity,
  setSelectedEntity,
  onConfirm,
  isPending,
}: EntityActionDialogProps) {
  return (
    <AlertDialog
      open={open}
      onOpenChange={(isOpen) => {
        if (!isOpen) setSelectedEntity(null)
        onOpenChange(isOpen)
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete entity permanently</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to permanently delete the entity{" "}
            <strong>{selectedEntity?.display_name}</strong>? This action cannot
            be undone. All fields, records, and associated data will be
            permanently deleted.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isPending}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={async () => {
              if (!selectedEntity) return
              await onConfirm(selectedEntity.id)
              setSelectedEntity(null)
            }}
            disabled={isPending}
          >
            Delete Permanently
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
