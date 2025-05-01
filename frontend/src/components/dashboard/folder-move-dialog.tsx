import React, { useEffect, useState } from "react"
import { FolderDirectoryItem } from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { ChevronDownIcon, FolderIcon } from "lucide-react"

import { useFolders } from "@/lib/hooks"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { toast } from "@/components/ui/use-toast"
import {
  FileTreeCommand,
  getFileTreeItems,
} from "@/components/dashboard/file-tree-command"

interface FolderMoveDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  selectedFolder: FolderDirectoryItem | null
  setSelectedFolder: (folder: FolderDirectoryItem | null) => void
}

export function FolderMoveDialog({
  open,
  onOpenChange,
  selectedFolder,
  setSelectedFolder,
}: FolderMoveDialogProps) {
  const { workspaceId } = useWorkspace()
  const { folders, moveFolder } = useFolders(workspaceId)
  const [destinationPath, setDestinationPath] = useState<string>("/")
  const [isMoving, setIsMoving] = useState(false)
  const [openFolderSelect, setOpenFolderSelect] = useState(false)

  // Get the current parent path
  const getCurrentParentPath = (): string => {
    if (!selectedFolder || selectedFolder.path === "/") return "/"

    const lastSlashIndex = selectedFolder.path.lastIndexOf("/")
    if (lastSlashIndex <= 0) return "/"

    return selectedFolder.path.substring(0, lastSlashIndex) || "/"
  }

  const currentParentPath = getCurrentParentPath()

  useEffect(() => {
    // Reset destination path when dialog opens or selected folder changes
    if (open) {
      setDestinationPath("/")
    }
  }, [open, selectedFolder])

  const handleClose = () => {
    if (!isMoving) {
      onOpenChange(false)
      // Delay clearing selection to avoid UI flicker if dialog closes quickly
      setTimeout(() => setSelectedFolder(null), 150)
    }
  }

  const handleMove = async () => {
    if (!selectedFolder || !workspaceId) {
      toast({
        title: "Error",
        description: "No folder selected or workspace context missing.",
        variant: "destructive",
      })
      return
    }

    // Prevent moving a folder into itself or its descendants
    if (
      destinationPath === selectedFolder.path ||
      destinationPath.startsWith(`${selectedFolder.path}/`)
    ) {
      toast({
        title: "Invalid destination",
        description:
          "Cannot move a folder into itself or one of its subfolders.",
        variant: "destructive",
      })
      return
    }

    setIsMoving(true)
    try {
      await moveFolder({
        folderId: selectedFolder.id,
        newParentPath: destinationPath,
      })

      toast({
        title: "Folder moved",
        description: `Successfully moved "${selectedFolder.name}" to ${destinationPath === "/" ? "root" : destinationPath}`,
      })

      handleClose()
    } catch (error) {
      // Error toast is handled by the mutation hook
      console.error("Failed to move folder:", error)
    } finally {
      setIsMoving(false)
    }
  }

  // Filter out the selected folder and its descendants
  const availableFolders = folders?.filter(
    (folder) =>
      folder.id !== selectedFolder?.id && // Not the folder itself
      !folder.path.startsWith(`${selectedFolder?.path}/`) // Not a descendant
  )

  const fileTreeItems = getFileTreeItems(availableFolders)

  const handleSelectFolder = (path: string) => {
    setDestinationPath(path)
    setOpenFolderSelect(false)
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Move Folder</DialogTitle>
          <DialogDescription>
            Select a new destination folder for{" "}
            <span className="font-medium">{selectedFolder?.name}</span>.
            <div className="mt-2 text-xs">
              Current location:{" "}
              <span className="font-medium">
                {currentParentPath === "/" ? "Root" : currentParentPath}
              </span>
            </div>
          </DialogDescription>
        </DialogHeader>

        <div className="flex items-center py-4">
          <Popover open={openFolderSelect} onOpenChange={setOpenFolderSelect}>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                role="combobox"
                aria-expanded={openFolderSelect}
                className="w-full justify-between"
                disabled={isMoving}
              >
                {destinationPath ? (
                  <div className="flex items-center gap-2">
                    <FolderIcon className="size-4" />
                    {destinationPath === "/" ? "Root" : destinationPath}
                  </div>
                ) : (
                  <div className="flex items-center gap-2">
                    <FolderIcon className="size-4" />
                    Root (/)
                  </div>
                )}
                <ChevronDownIcon className="ml-2 size-4 shrink-0 opacity-50" />
              </Button>
            </PopoverTrigger>
            <PopoverContent className="max-h-[300px] w-[--radix-popover-trigger-width] overflow-y-auto p-0">
              <FileTreeCommand
                items={fileTreeItems}
                onSelect={handleSelectFolder}
              />
            </PopoverContent>
          </Popover>
        </div>

        <DialogFooter className="sm:justify-end">
          <Button variant="secondary" onClick={handleClose} disabled={isMoving}>
            Cancel
          </Button>
          <Button onClick={handleMove} disabled={isMoving}>
            {isMoving ? "Moving..." : "Move Folder"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
