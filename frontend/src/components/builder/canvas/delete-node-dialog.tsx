import { PropsWithoutRef } from "react"
import { AlertDialogProps } from "@radix-ui/react-alert-dialog"

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

interface DeleteNodeDialogProps extends PropsWithoutRef<AlertDialogProps> {
  onConfirm: () => void
}

export function DeleteActionNodeDialog({
  onConfirm,
  ...rest
}: DeleteNodeDialogProps) {
  return (
    <AlertDialog {...rest}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete action node</AlertDialogTitle>
          <AlertDialogDescription className="space-y-2">
            Are you sure you want to delete this action node? This will remove
            it from the workflow and delete any connections to other nodes.
            <br />
            <br />
            <strong>This action cannot be undone.</strong>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={onConfirm}>
            Delete
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
