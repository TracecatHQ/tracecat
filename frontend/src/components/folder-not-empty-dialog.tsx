"use client"

import { CopyButton } from "@/components/copy-button"
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"

/**
 * Shown when the user tries to delete a folder whose contents cannot be
 * deleted with it. Empty folders are deleted directly without a dialog.
 */
export function FolderNotEmptyDialog({
  open,
  onOpenChange,
  folderId,
  folderName,
  numItems,
  itemNoun,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  folderId: string
  folderName: string
  numItems: number
  /** Plural noun for the folder's contents, e.g. "agents or folders". */
  itemNoun: string
}) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Folder is not empty</AlertDialogTitle>
          <AlertDialogDescription>
            <span className="font-medium text-foreground">{folderName}</span>{" "}
            contains {numItems} {numItems === 1 ? "item" : "items"}. Move or
            delete the {itemNoun} inside it first, then delete the folder.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="flex items-center justify-between gap-3 rounded-md border bg-muted/40 px-3 py-2">
          <div className="flex min-w-0 flex-col">
            <span className="text-xs text-muted-foreground">Folder ID</span>
            <code className="truncate font-mono text-sm">{folderId}</code>
          </div>
          <CopyButton
            value={folderId}
            toastMessage="Copied folder ID to clipboard"
            tooltipMessage="Copy folder ID"
            className="size-7 shrink-0"
            iconClassName="size-3.5"
          />
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel>Close</AlertDialogCancel>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
