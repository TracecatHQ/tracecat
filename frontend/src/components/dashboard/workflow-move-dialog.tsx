"use client"

import { ChevronDownIcon, FolderIcon } from "lucide-react"
import { useEffect, useState } from "react"
import type { WorkflowReadMinimal } from "@/client"
import {
  FileTreeCommand,
  getFileTreeItems,
  ROOT_FOLDER_NAME,
} from "@/components/dashboard/file-tree-command"
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
import { useFolders, useWorkflowManager } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

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
  const workspaceId = useWorkspaceId()
  const { moveWorkflow } = useWorkflowManager(undefined, {
    listEnabled: false,
  })
  const { folders } = useFolders(workspaceId, { enabled: open })
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

        <div className="w-full flex items-center py-4">
          <Popover open={openFolderSelect} onOpenChange={setOpenFolderSelect}>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                role="combobox"
                aria-expanded={openFolderSelect}
                className="flex w-96 max-w-full min-w-0 justify-between overflow-hidden"
              >
                {selectedFolder ? (
                  <div className="flex min-w-0 flex-1 items-center gap-2">
                    <FolderIcon className="size-4 shrink-0" />
                    <span
                      className="truncate"
                      title={
                        selectedFolder === "/"
                          ? ROOT_FOLDER_NAME
                          : selectedFolder
                      }
                    >
                      {selectedFolder === "/"
                        ? ROOT_FOLDER_NAME
                        : selectedFolder}
                    </span>
                  </div>
                ) : (
                  <div className="flex items-center gap-2">
                    Select a folder...
                  </div>
                )}
                <ChevronDownIcon className="ml-2 size-4 shrink-0 opacity-50" />
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-[--radix-popover-trigger-width] overflow-hidden p-0">
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
