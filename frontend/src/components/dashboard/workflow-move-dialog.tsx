"use client"

import React, { useEffect, useState } from "react"
import { WorkflowReadMinimal } from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { ChevronDownIcon, FolderIcon } from "lucide-react"

import { useFolders, useWorkflowManager } from "@/lib/hooks"
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
  ROOT_FOLDER_NAME,
} from "@/components/dashboard/file-tree-command"

interface WorkflowMoveDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  selectedWorkflow: WorkflowReadMinimal | null
  setSelectedWorkflow: (workflow: WorkflowReadMinimal | null) => void
}

export function WorkflowMoveDialog({
  open,
  onOpenChange,
  selectedWorkflow,
  setSelectedWorkflow,
}: WorkflowMoveDialogProps) {
  const { workspaceId } = useWorkspace()
  const { moveWorkflow } = useWorkflowManager()
  const { folders } = useFolders(workspaceId)
  const [isLoading, setIsLoading] = useState(false)
  const [selectedFolder, setSelectedFolder] = useState<string | null>(null)
  const [openFolderSelect, setOpenFolderSelect] = useState(false)

  // Reset state when dialog opens
  useEffect(() => {
    if (open) {
      setSelectedFolder(null)
    }
  }, [open])

  const handleMove = async () => {
    if (!selectedWorkflow) return

    setIsLoading(true)
    try {
      await moveWorkflow({
        workflowId: selectedWorkflow.id,
        workspaceId,
        requestBody: {
          folder_path: selectedFolder || "/",
        },
      })

      toast({
        title: "Workflow moved",
        description: `Successfully moved "${selectedWorkflow.title}" to ${selectedFolder || "root"}`,
      })

      onOpenChange(false)
      setSelectedWorkflow(null)
    } catch (error) {
      console.error("Failed to move workflow:", error)
      toast({
        title: "Error",
        description: "Failed to move workflow",
        variant: "destructive",
      })
    } finally {
      setIsLoading(false)
    }
  }

  const fileTreeItems = getFileTreeItems(folders)

  const handleSelectFolder = (path: string) => {
    setSelectedFolder(path)
    setOpenFolderSelect(false)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Move Workflow</DialogTitle>
          <DialogDescription>
            Choose a folder to move{" "}
            <span className="font-medium">{selectedWorkflow?.title}</span> to
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
              >
                {selectedFolder ? (
                  <div className="flex items-center gap-2">
                    <FolderIcon className="size-4" />
                    {selectedFolder === "/" ? ROOT_FOLDER_NAME : selectedFolder}
                  </div>
                ) : (
                  <div className="flex items-center gap-2">
                    Select a folder...
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
          <Button
            variant="secondary"
            onClick={() => onOpenChange(false)}
            disabled={isLoading}
          >
            Cancel
          </Button>
          <Button onClick={handleMove} disabled={isLoading || !selectedFolder}>
            {isLoading ? "Moving..." : "Move"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
