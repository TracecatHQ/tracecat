"use client"

import React, { useState } from "react"
import { FolderDirectoryItem } from "@/client"
import { useWorkspace } from "@/providers/workspace"

import { useFolders } from "@/lib/hooks"
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
import { Input } from "@/components/ui/input"

export function FolderDeleteAlertDialog({
  open,
  onOpenChange,
  selectedFolder,
  setSelectedFolder,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  selectedFolder: FolderDirectoryItem | null
  setSelectedFolder: (selectedFolder: FolderDirectoryItem | null) => void
}) {
  const { workspaceId } = useWorkspace()
  const { deleteFolder } = useFolders(workspaceId)
  const [confirmName, setConfirmName] = useState("")

  return (
    <AlertDialog
      open={open}
      onOpenChange={(isOpen) => {
        if (!isOpen) {
          setSelectedFolder(null)
        }
        onOpenChange(isOpen)
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete folder</AlertDialogTitle>
          <AlertDialogDescription>
            <span>
              Are you sure you want to delete this folder? This action cannot be
              undone.
            </span>
            <span>
              You cannot delete a folder that contains workflows or other
              folders.
            </span>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="my-4">
          <Input
            placeholder={`Type "${selectedFolder?.name}" to confirm`}
            value={confirmName}
            onChange={(e) => setConfirmName(e.target.value)}
          />
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={!selectedFolder || confirmName !== selectedFolder?.name}
            onClick={async () => {
              if (selectedFolder) {
                console.debug("Deleting folder", selectedFolder)
                try {
                  await deleteFolder({
                    folderId: selectedFolder.id,
                  })
                } catch (error) {
                  console.log("Failed to delete folder:", error)
                }
              }
              setSelectedFolder(null)
              setConfirmName("")
            }}
          >
            Confirm
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
