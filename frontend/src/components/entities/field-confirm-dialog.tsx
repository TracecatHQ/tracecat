"use client"

import type { EntityFieldRead } from "@/client"
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

interface FieldActionDialogProps extends BaseProps {
  selectedField: EntityFieldRead | null
  setSelectedField: (field: EntityFieldRead | null) => void
  onConfirm: (fieldId: string) => Promise<void>
}

export function FieldArchiveAlertDialog({
  open,
  onOpenChange,
  selectedField,
  setSelectedField,
  onConfirm,
  isPending,
}: FieldActionDialogProps) {
  return (
    <AlertDialog
      open={open}
      onOpenChange={(isOpen) => {
        if (!isOpen) setSelectedField(null)
        onOpenChange(isOpen)
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Archive field</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to archive the field{" "}
            <strong>{selectedField?.display_name}</strong>? The field will be
            hidden but data will be preserved.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isPending}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={async () => {
              if (!selectedField) return
              await onConfirm(selectedField.id)
              setSelectedField(null)
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

export function FieldDeleteAlertDialog({
  open,
  onOpenChange,
  selectedField,
  setSelectedField,
  onConfirm,
  isPending,
}: FieldActionDialogProps) {
  return (
    <AlertDialog
      open={open}
      onOpenChange={(isOpen) => {
        if (!isOpen) setSelectedField(null)
        onOpenChange(isOpen)
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete field permanently</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to permanently delete the field{" "}
            <strong>{selectedField?.display_name}</strong>? This action cannot
            be undone and will delete all existing values for this field across
            all records.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isPending}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={async () => {
              if (!selectedField) return
              await onConfirm(selectedField.id)
              setSelectedField(null)
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
