"use client"

import { FolderIcon } from "lucide-react"
import { useEffect, useState } from "react"
import type { TableFolderDirectoryItem, TableReadMinimal } from "@/client"
import {
  FileTreeCommand,
  getFileTreeItems,
  ROOT_FOLDER_NAME,
} from "@/components/dashboard/file-tree-command"
import { Spinner } from "@/components/loading/spinner"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { useMoveTable, useTableFolders } from "@/hooks/use-table-folders"
import { useWorkspaceId } from "@/providers/workspace-id"

/**
 * Normalize a table folder path from the URL to the API's `/a/b/` form.
 */
export function normalizeTableFolderPath(rawPath: string | null): string {
  if (!rawPath || rawPath === "/") return "/"
  const trimmed = rawPath.replace(/^\/+|\/+$/g, "")
  return trimmed ? `/${trimmed}/` : "/"
}

function FolderPathSelect({
  value,
  onChange,
  folders,
  disabled,
}: {
  value: string
  onChange: (path: string) => void
  folders: Parameters<typeof getFileTreeItems>[0]
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  return (
    <Popover modal open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className="w-full justify-between"
          disabled={disabled}
        >
          <span className="flex min-w-0 items-center gap-2 truncate">
            <FolderIcon className="size-4 shrink-0" />
            {value === "/" ? ROOT_FOLDER_NAME : value}
          </span>
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className="w-[--radix-popover-trigger-width] overflow-hidden p-0"
        portal={true}
      >
        <FileTreeCommand
          items={getFileTreeItems(folders)}
          onSelect={(path) => {
            onChange(path)
            setOpen(false)
          }}
        />
      </PopoverContent>
    </Popover>
  )
}

export function TableFolderCreateDialog({
  open,
  onOpenChange,
  parentPath,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  parentPath: string
}) {
  const workspaceId = useWorkspaceId()
  const { createFolder, createFolderIsPending } = useTableFolders(workspaceId, {
    enabled: false,
  })
  const [name, setName] = useState("")

  useEffect(() => {
    if (open) setName("")
  }, [open])

  const handleCreate = async () => {
    try {
      await createFolder({ name: name.trim(), parent_path: parentPath })
      onOpenChange(false)
    } catch {
      // The mutation hook reports failures.
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="sm">
        <DialogHeader>
          <DialogTitle>Create folder</DialogTitle>
          <DialogDescription>
            Create a new folder to organize your tables.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <Input
            autoFocus
            placeholder="Folder name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && name.trim()) void handleCreate()
            }}
          />
        </DialogBody>
        <DialogFooter>
          <Button
            size="sm"
            onClick={() => void handleCreate()}
            disabled={createFolderIsPending || !name.trim()}
          >
            {createFolderIsPending ? <Spinner className="mr-2 size-4" /> : null}
            Create folder
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function TableFolderRenameDialog({
  open,
  onOpenChange,
  folder,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  folder: TableFolderDirectoryItem
}) {
  const workspaceId = useWorkspaceId()
  const { updateFolder, updateFolderIsPending } = useTableFolders(workspaceId, {
    enabled: false,
  })
  const [name, setName] = useState(folder.name)

  useEffect(() => {
    if (open) setName(folder.name)
  }, [folder.name, open])

  const handleRename = async () => {
    try {
      await updateFolder({ folderId: folder.id, name: name.trim() })
      onOpenChange(false)
    } catch {
      // The mutation hook reports failures.
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="sm">
        <DialogHeader>
          <DialogTitle>Rename folder</DialogTitle>
          <DialogDescription>
            Enter a new name for the folder.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <Input
            autoFocus
            value={name}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && name.trim()) void handleRename()
            }}
          />
        </DialogBody>
        <DialogFooter>
          <Button
            size="sm"
            onClick={() => void handleRename()}
            disabled={updateFolderIsPending || !name.trim()}
          >
            {updateFolderIsPending ? <Spinner className="mr-2 size-4" /> : null}
            Rename
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function TableFolderMoveDialog({
  open,
  onOpenChange,
  folder,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  folder: TableFolderDirectoryItem
}) {
  const workspaceId = useWorkspaceId()
  const { folders, moveFolder, moveFolderIsPending } = useTableFolders(
    workspaceId,
    { enabled: open }
  )
  const [destinationPath, setDestinationPath] = useState("/")
  const availableFolders = folders?.filter(
    (candidate) =>
      candidate.id !== folder.id && !candidate.path.startsWith(folder.path)
  )

  useEffect(() => {
    if (open) setDestinationPath("/")
  }, [folder.id, open])

  const handleMove = async () => {
    try {
      await moveFolder({ folderId: folder.id, newParentPath: destinationPath })
      onOpenChange(false)
    } catch {
      // The mutation hook reports failures.
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="sm">
        <DialogHeader>
          <DialogTitle>Move folder</DialogTitle>
          <DialogDescription>
            Select a new destination for{" "}
            <span className="font-medium">{folder.name}</span>.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <FolderPathSelect
            value={destinationPath}
            onChange={setDestinationPath}
            folders={availableFolders}
            disabled={moveFolderIsPending}
          />
        </DialogBody>
        <DialogFooter>
          <Button
            size="sm"
            onClick={() => void handleMove()}
            disabled={moveFolderIsPending}
          >
            {moveFolderIsPending ? <Spinner className="mr-2 size-4" /> : null}
            Move
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function TableMoveDialog({
  open,
  onOpenChange,
  table,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  table: Pick<TableReadMinimal, "id" | "name">
}) {
  const workspaceId = useWorkspaceId()
  const { folders } = useTableFolders(workspaceId, { enabled: open })
  const { moveTable, moveTableIsPending } = useMoveTable(workspaceId)
  const [destinationPath, setDestinationPath] = useState("/")

  useEffect(() => {
    if (open) setDestinationPath("/")
  }, [open, table.id])

  const handleMove = async () => {
    try {
      await moveTable({ tableId: table.id, folder_path: destinationPath })
      onOpenChange(false)
    } catch {
      // The mutation hook reports failures.
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="sm">
        <DialogHeader>
          <DialogTitle>Move table</DialogTitle>
          <DialogDescription>
            Select a folder for{" "}
            <span className="font-medium">{table.name}</span>. Workflows keep
            referencing the table by name.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <FolderPathSelect
            value={destinationPath}
            onChange={setDestinationPath}
            folders={folders}
            disabled={moveTableIsPending}
          />
        </DialogBody>
        <DialogFooter>
          <Button
            size="sm"
            onClick={() => void handleMove()}
            disabled={moveTableIsPending}
          >
            {moveTableIsPending ? <Spinner className="mr-2 size-4" /> : null}
            Move
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
