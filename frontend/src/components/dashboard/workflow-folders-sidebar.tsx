"use client"

import {
  FileIcon,
  FolderIcon,
  FolderTreeIcon,
  MoreHorizontalIcon,
  PlusIcon,
} from "lucide-react"
import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useFolders } from "@/lib/hooks"
import { cn } from "@/lib/utils"

interface FolderItemProps {
  folderId: string
  name: string
  path: string
  workspaceId: string
  active: boolean
  depth?: number
}

function FolderItem({
  folderId,
  name,
  path,
  workspaceId,
  active,
  depth = 0,
}: FolderItemProps) {
  const { deleteFolder, updateFolder } = useFolders(workspaceId)
  const [isRenaming, setIsRenaming] = useState(false)
  const [newName, setNewName] = useState(name)

  const handleRename = async () => {
    if (newName && newName !== name) {
      await updateFolder({ folderId, name: newName })
    }
    setIsRenaming(false)
  }

  const handleDelete = async () => {
    if (confirm(`Are you sure you want to delete folder "${name}"?`)) {
      try {
        await deleteFolder({
          folderId,
        })
      } catch (error) {
        console.error("Error deleting folder:", error)
      }
    }
  }

  return (
    <div className="group relative flex items-center gap-1 pl-2">
      {depth > 0 && (
        <div
          className="absolute h-full border-l border-dashed border-muted-foreground/20"
          style={{ left: `${depth * 12 - 8}px`, top: 0 }}
        />
      )}

      <div className="flex w-full items-center justify-between py-1">
        <Link
          href={`/workspaces/${workspaceId}/workflows?folderId=${folderId}`}
          className={cn(
            "relative flex flex-1 items-center gap-2 rounded-md px-2 py-1.5 text-sm font-medium hover:bg-muted/50",
            active && "bg-muted text-foreground"
          )}
          style={{ paddingLeft: `${depth * 12 + 8}px` }}
        >
          <FolderIcon className="size-4 shrink-0 text-muted-foreground" />
          {isRenaming ? (
            <form
              onSubmit={(e) => {
                e.preventDefault()
                handleRename()
              }}
              className="flex-1"
              onClick={(e) => e.stopPropagation()}
            >
              <Input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                className="h-7 px-2 py-1 text-xs"
                autoFocus
                onBlur={handleRename}
              />
            </form>
          ) : (
            <span className="truncate">{name}</span>
          )}
        </Link>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="size-8 p-0 opacity-0 group-hover:opacity-100"
            >
              <MoreHorizontalIcon className="size-4" />
              <span className="sr-only">Actions</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={() => setIsRenaming(true)}>
              Rename
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={handleDelete}
              className="text-destructive"
            >
              Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  )
}

interface CreateFolderDialogProps {
  workspaceId: string
  parentPath?: string
}

function CreateFolderDialog({
  workspaceId,
  parentPath = "/",
}: CreateFolderDialogProps) {
  const [folderName, setFolderName] = useState("")
  const [open, setOpen] = useState(false)
  const { createFolder } = useFolders(workspaceId)

  const handleCreateFolder = async () => {
    if (!folderName.trim()) return

    try {
      await createFolder({
        name: folderName.trim(),
        parent_path: parentPath,
      })
      setFolderName("")
      setOpen(false)
    } catch (error) {
      console.error("Error creating folder:", error)
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="w-full">
          <PlusIcon className="mr-2 size-4" />
          New Folder
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle>Create new folder</DialogTitle>
        </DialogHeader>
        <div className="grid gap-4 py-4">
          <div className="grid gap-2">
            <Input
              id="folder-name"
              placeholder="Folder name"
              className="col-span-3"
              value={folderName}
              onChange={(e) => setFolderName(e.target.value)}
              autoFocus
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button onClick={handleCreateFolder} disabled={!folderName.trim()}>
            Create
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function WorkflowFoldersSidebar({
  workspaceId,
}: {
  workspaceId: string
}) {
  const searchParams = useSearchParams()
  const currentFolderPath = searchParams.get("folder") || "/"

  const { subFolders, foldersIsLoading } = useFolders(workspaceId)

  return (
    <div className="flex size-full flex-col gap-2 text-sm">
      <div className="flex items-center justify-between py-2">
        <div className="flex items-center gap-1 text-muted-foreground">
          <FolderTreeIcon className="size-4" />
          <h3 className="text-base font-medium">Folders</h3>
        </div>
      </div>
      <Separator />

      <div className="flex flex-col gap-1 py-2">
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Link
                href={`/workspaces/${workspaceId}/workflows`}
                className={cn(
                  "flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium hover:bg-muted/50",
                  currentFolderPath === "/" && "bg-muted text-foreground"
                )}
              >
                <FileIcon className="size-4 shrink-0 text-muted-foreground" />
                <span className="truncate">All Workflows</span>
              </Link>
            </TooltipTrigger>
            <TooltipContent side="right">
              <p>View all workflows</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </div>

      <Separator className="my-1" />

      <div className="mb-4">
        <CreateFolderDialog workspaceId={workspaceId} />
      </div>

      <div className="overflow-auto">
        {foldersIsLoading ? (
          <div className="flex items-center justify-center py-4">
            <span className="text-sm text-muted-foreground">
              Loading folders...
            </span>
          </div>
        ) : subFolders && subFolders.length > 0 ? (
          <div className="space-y-1">
            {subFolders.map((folder) => (
              <FolderItem
                key={folder.id}
                folderId={folder.id}
                name={folder.name}
                path={folder.path}
                workspaceId={workspaceId}
                active={currentFolderPath === folder.path}
              />
            ))}
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center gap-2 py-8 text-center text-muted-foreground">
            <FolderIcon className="size-8" />
            <div>
              <p>No folders</p>
              <p className="text-xs">
                Create folders to organize your workflows
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
