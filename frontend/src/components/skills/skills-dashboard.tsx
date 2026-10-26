"use client"

import {
  BoxIcon,
  CircleCheck,
  CircleDot,
  Copy,
  ExternalLinkIcon,
  FolderIcon,
  FolderKanban,
  Pencil,
  Pyramid,
  TagsIcon,
  Trash2,
} from "lucide-react"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import { useCallback, useEffect, useMemo, useState } from "react"
import type {
  SkillDirectoryItem,
  SkillFolderDirectoryItem,
  SkillReadMinimal,
  SkillTagRead,
  TagRead,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import {
  FileTreeCommand,
  getFileTreeItems,
  ROOT_FOLDER_NAME,
} from "@/components/dashboard/file-tree-command"
import { FolderNotEmptyDialog } from "@/components/folder-not-empty-dialog"
import { CenteredSpinner, Spinner } from "@/components/loading/spinner"
import { DeleteSkillDialog } from "@/components/skills/delete-skill-dialog"
import {
  DEFAULT_SKILL_SORT,
  SkillsHeader,
  type SkillsSortValue,
  type SkillsViewMode,
} from "@/components/skills/skills-header"
import { useSkillAgentFilter } from "@/components/skills/use-skill-agent-filter"
import { RowTimestamps } from "@/components/timestamps"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  ContextMenu,
  ContextMenuCheckboxItem,
  ContextMenuContent,
  ContextMenuGroup,
  ContextMenuItem,
  ContextMenuPortal,
  ContextMenuSeparator,
  ContextMenuSub,
  ContextMenuSubContent,
  ContextMenuSubTrigger,
  ContextMenuTrigger,
} from "@/components/ui/context-menu"
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Empty,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { toast } from "@/components/ui/use-toast"
import { useEntitlements } from "@/hooks/use-entitlements"
import {
  type SkillDirectoryEntry,
  useMoveSkill,
  useSkillDirectoryItems,
  useSkillFolders,
} from "@/hooks/use-skill-folders"
import {
  useSkillTagCatalog,
  useSkillTagMutations,
} from "@/hooks/use-skill-tags"
import { useDeleteSkill, useSkills } from "@/hooks/use-skills"
import { matchesSkillAgentFilter } from "@/lib/skill-catalog-filters"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

type SkillActiveDialog =
  | "folder-create"
  | "folder-rename"
  | "folder-move"
  | "skill-move"
  | "delete"
  | null

const ROW_NAME_COLUMN_CLASS = "min-w-0 w-[340px] shrink-0 truncate text-xs"

function normalizeSkillFolderPath(rawPath: string | null): string {
  if (!rawPath || rawPath === "/") {
    return "/"
  }
  const withLeadingSlash = rawPath.startsWith("/") ? rawPath : `/${rawPath}`
  return withLeadingSlash.endsWith("/")
    ? withLeadingSlash.slice(0, -1)
    : withLeadingSlash
}

function parseSkillsViewMode(value: string | null): SkillsViewMode {
  return value === "list" ? "list" : "folders"
}

function compareSkillItems(
  a: SkillDirectoryEntry,
  b: SkillDirectoryEntry,
  sortBy: SkillsSortValue
): number {
  if (a.type !== b.type) {
    return a.type === "folder" ? -1 : 1
  }
  const direction = sortBy.direction === "asc" ? 1 : -1
  if (sortBy.field === "name") {
    return (
      a.name.localeCompare(b.name, undefined, {
        numeric: true,
        sensitivity: "base",
      }) * direction
    )
  }
  const aTimestamp = Date.parse(a[sortBy.field])
  const bTimestamp = Date.parse(b[sortBy.field])
  if (aTimestamp !== bTimestamp) {
    if (Number.isNaN(aTimestamp) && Number.isNaN(bTimestamp)) return 0
    if (Number.isNaN(aTimestamp)) return 1
    if (Number.isNaN(bTimestamp)) return -1
    return (aTimestamp - bTimestamp) * direction
  }
  return (
    a.name.localeCompare(b.name, undefined, {
      numeric: true,
      sensitivity: "base",
    }) * direction
  )
}

function SkillTagPills({ tags }: { tags: TagRead[] }) {
  if (tags.length === 0) return null
  return (
    <div className="flex min-w-0 items-center gap-1">
      {tags.slice(0, 3).map((tag) => (
        <span
          key={tag.id}
          className={cn(
            "inline-flex h-5 max-w-[110px] items-center truncate rounded-full px-2 text-[10px] font-medium",
            !tag.color && "bg-muted text-muted-foreground"
          )}
          style={
            tag.color
              ? { backgroundColor: `${tag.color}20`, color: tag.color }
              : undefined
          }
        >
          {tag.name}
        </span>
      ))}
      {tags.length > 3 ? (
        <span className="text-[10px] text-muted-foreground">
          +{tags.length - 3}
        </span>
      ) : null}
    </div>
  )
}

function SkillMetadataBadges({ item }: { item: SkillDirectoryItem }) {
  return (
    <div className="flex shrink-0 items-center gap-1">
      {item.current_version_id ? (
        <Badge variant="secondary" className="h-5 px-2 text-[10px] font-normal">
          <CircleCheck className="mr-1 size-3" />
          Published
        </Badge>
      ) : (
        <Badge variant="secondary" className="h-5 px-2 text-[10px] font-normal">
          <CircleDot className="mr-1 size-3" />
          Unpublished
        </Badge>
      )}
    </div>
  )
}

/**
 * Dialog for creating a skill folder.
 *
 * @param props Dialog properties.
 * @returns Folder creation dialog.
 */
export function SkillFolderCreateDialog({
  open,
  onOpenChange,
  currentPath,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  currentPath: string
}) {
  const workspaceId = useWorkspaceId()
  const { createFolder, createFolderIsPending } = useSkillFolders(workspaceId, {
    enabled: open,
  })
  const [name, setName] = useState("")

  useEffect(() => {
    if (open) setName("")
  }, [open])

  const handleSubmit = async () => {
    if (!name.trim()) return
    try {
      await createFolder({
        name: name.trim(),
        parent_path: currentPath === "/" ? undefined : currentPath,
      })
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
            Create a new folder to organize your skills.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="py-2">
            <Input
              placeholder="Folder name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void handleSubmit()
              }}
            />
          </div>
        </DialogBody>
        <DialogFooter>
          <Button
            size="sm"
            onClick={() => void handleSubmit()}
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

function SkillFolderRenameDialog({
  open,
  onOpenChange,
  folder,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  folder: SkillFolderDirectoryItem
}) {
  const workspaceId = useWorkspaceId()
  const { updateFolder, updateFolderIsPending } = useSkillFolders(workspaceId, {
    enabled: open,
  })
  const [name, setName] = useState(folder.name)

  useEffect(() => {
    if (open) setName(folder.name)
  }, [folder.name, open])

  const handleSubmit = async () => {
    if (!name.trim()) return
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
          <div className="py-2">
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void handleSubmit()
              }}
            />
          </div>
        </DialogBody>
        <DialogFooter>
          <Button
            size="sm"
            onClick={() => void handleSubmit()}
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

function SkillFolderMoveDialog({
  open,
  onOpenChange,
  folder,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  folder: SkillFolderDirectoryItem
}) {
  const workspaceId = useWorkspaceId()
  const { folders, moveFolder, moveFolderIsPending } = useSkillFolders(
    workspaceId,
    { enabled: open }
  )
  const [destinationPath, setDestinationPath] = useState("/")
  const [selectorOpen, setSelectorOpen] = useState(false)
  const availableFolders = folders?.filter(
    (candidate) =>
      candidate.id !== folder.id &&
      !candidate.path.startsWith(`${folder.path}/`)
  )

  useEffect(() => {
    if (open) setDestinationPath("/")
  }, [folder.id, open])

  const handleMove = async () => {
    try {
      await moveFolder({
        folderId: folder.id,
        newParentPath: destinationPath,
      })
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
          <Popover modal open={selectorOpen} onOpenChange={setSelectorOpen}>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                role="combobox"
                aria-expanded={selectorOpen}
                className="w-full justify-between"
                disabled={moveFolderIsPending}
              >
                <span className="flex min-w-0 items-center gap-2 truncate">
                  <FolderIcon className="size-4 shrink-0" />
                  {destinationPath === "/" ? ROOT_FOLDER_NAME : destinationPath}
                </span>
              </Button>
            </PopoverTrigger>
            <PopoverContent
              className="w-[--radix-popover-trigger-width] overflow-hidden p-0"
              portal={true}
            >
              <FileTreeCommand
                items={getFileTreeItems(availableFolders)}
                onSelect={(path) => {
                  setDestinationPath(path)
                  setSelectorOpen(false)
                }}
              />
            </PopoverContent>
          </Popover>
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

function SkillMoveDialog({
  open,
  onOpenChange,
  skill,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  skill: SkillDirectoryItem
}) {
  const workspaceId = useWorkspaceId()
  const { folders } = useSkillFolders(workspaceId, { enabled: open })
  const { moveSkill, moveSkillIsPending } = useMoveSkill(workspaceId)
  const [destinationPath, setDestinationPath] = useState("/")
  const [selectorOpen, setSelectorOpen] = useState(false)

  useEffect(() => {
    if (open) setDestinationPath("/")
  }, [open, skill.id])

  const handleMove = async () => {
    try {
      await moveSkill({ skillId: skill.id, folder_path: destinationPath })
      onOpenChange(false)
    } catch {
      // The mutation hook reports failures.
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="sm">
        <DialogHeader>
          <DialogTitle>Move skill</DialogTitle>
          <DialogDescription>
            Select a folder for{" "}
            <span className="font-medium">{skill.name}</span>.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <Popover modal open={selectorOpen} onOpenChange={setSelectorOpen}>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                role="combobox"
                aria-expanded={selectorOpen}
                className="w-full justify-between"
                disabled={moveSkillIsPending}
              >
                <span className="flex min-w-0 items-center gap-2 truncate">
                  <FolderIcon className="size-4 shrink-0" />
                  {destinationPath === "/" ? ROOT_FOLDER_NAME : destinationPath}
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
                  setDestinationPath(path)
                  setSelectorOpen(false)
                }}
              />
            </PopoverContent>
          </Popover>
        </DialogBody>
        <DialogFooter>
          <Button
            size="sm"
            onClick={() => void handleMove()}
            disabled={moveSkillIsPending}
          >
            {moveSkillIsPending ? <Spinner className="mr-2 size-4" /> : null}
            Move
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function SkillFolderContextActions({
  item,
  setActiveDialog,
  setSelectedItem,
  canUpdateSkill,
  canDeleteSkill,
}: {
  item: SkillFolderDirectoryItem
  setActiveDialog: (dialog: SkillActiveDialog) => void
  setSelectedItem: (item: SkillDirectoryEntry) => void
  canUpdateSkill: boolean
  canDeleteSkill: boolean
}) {
  const workspaceId = useWorkspaceId()
  const { deleteFolder } = useSkillFolders(workspaceId, { enabled: false })
  return (
    <ContextMenuGroup>
      <ContextMenuItem
        className="text-xs"
        onSelect={(event) => {
          event.stopPropagation()
          void navigator.clipboard.writeText(item.id)
          toast({ title: "Copied", description: "Folder ID copied" })
        }}
      >
        <Copy className="mr-2 size-3.5" />
        Copy folder ID
      </ContextMenuItem>
      {canUpdateSkill ? (
        <>
          <ContextMenuItem
            className="text-xs"
            onSelect={(event) => {
              event.stopPropagation()
              setSelectedItem(item)
              setActiveDialog("folder-rename")
            }}
          >
            <Pencil className="mr-2 size-3.5" />
            Rename folder
          </ContextMenuItem>
          <ContextMenuItem
            className="text-xs"
            onSelect={(event) => {
              event.stopPropagation()
              setSelectedItem(item)
              setActiveDialog("folder-move")
            }}
          >
            <FolderKanban className="mr-2 size-3.5" />
            Move folder
          </ContextMenuItem>
        </>
      ) : null}
      {canDeleteSkill ? (
        <>
          <ContextMenuSeparator />
          <ContextMenuItem
            className="text-xs text-rose-500 focus:text-rose-600"
            onSelect={(event) => {
              event.stopPropagation()
              if (item.num_items === 0) {
                void deleteFolder({ folderId: item.id }).catch(() => {
                  // The mutation hook reports failures.
                })
                return
              }
              setSelectedItem(item)
              setActiveDialog("delete")
            }}
          >
            <Trash2 className="mr-2 size-3.5" />
            Delete
          </ContextMenuItem>
        </>
      ) : null}
    </ContextMenuGroup>
  )
}

function SkillContextActions({
  item,
  setActiveDialog,
  setSelectedItem,
  availableTags,
  areTagsLoading,
  canUpdateSkill,
  canDeleteSkill,
}: {
  item: SkillDirectoryItem
  setActiveDialog: (dialog: SkillActiveDialog) => void
  setSelectedItem: (item: SkillDirectoryEntry) => void
  availableTags?: SkillTagRead[]
  areTagsLoading: boolean
  canUpdateSkill: boolean
  canDeleteSkill: boolean
}) {
  const workspaceId = useWorkspaceId()
  const { addSkillTag, removeSkillTag } = useSkillTagMutations(workspaceId)

  return (
    <ContextMenuGroup>
      <ContextMenuItem className="text-xs" asChild>
        <Link
          href={`/workspaces/${workspaceId}/skills/${item.id}`}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(event) => event.stopPropagation()}
        >
          <ExternalLinkIcon className="mr-2 size-3.5" />
          Open in new tab
        </Link>
      </ContextMenuItem>
      {canUpdateSkill ? (
        <ContextMenuItem
          className="text-xs"
          onSelect={(event) => {
            event.stopPropagation()
            setSelectedItem(item)
            setActiveDialog("skill-move")
          }}
        >
          <FolderKanban className="mr-2 size-3.5" />
          Move to folder
        </ContextMenuItem>
      ) : null}
      {canUpdateSkill ? (
        availableTags && availableTags.length > 0 ? (
          <ContextMenuSub>
            <ContextMenuSubTrigger className="text-xs">
              <TagsIcon className="mr-2 size-3.5" />
              Tags
            </ContextMenuSubTrigger>
            <ContextMenuPortal>
              <ContextMenuSubContent>
                {availableTags.map((tag) => {
                  const hasTag = item.tags.some(
                    (assignedTag) => assignedTag.id === tag.id
                  )
                  return (
                    <ContextMenuCheckboxItem
                      key={tag.id}
                      className="text-xs"
                      checked={hasTag}
                      onClick={async (event) => {
                        event.stopPropagation()
                        try {
                          if (hasTag) {
                            await removeSkillTag({
                              skillId: item.id,
                              tagId: tag.id,
                            })
                          } else {
                            await addSkillTag({
                              skillId: item.id,
                              requestBody: { tag_id: tag.id },
                            })
                          }
                        } catch {
                          // The mutation hook reports failures.
                        }
                      }}
                    >
                      <div
                        className="mr-2 flex size-2 rounded-full"
                        style={{ backgroundColor: tag.color || undefined }}
                      />
                      <span>{tag.name}</span>
                    </ContextMenuCheckboxItem>
                  )
                })}
              </ContextMenuSubContent>
            </ContextMenuPortal>
          </ContextMenuSub>
        ) : areTagsLoading ? (
          <ContextMenuItem className="!bg-transparent text-xs !text-muted-foreground">
            <TagsIcon className="mr-2 size-3.5" />
            Loading tags...
          </ContextMenuItem>
        ) : (
          <ContextMenuItem className="!bg-transparent text-xs !text-muted-foreground">
            <TagsIcon className="mr-2 size-3.5" />
            No tags available
          </ContextMenuItem>
        )
      ) : null}
      <ContextMenuItem
        className="text-xs"
        onSelect={(event) => {
          event.stopPropagation()
          void navigator.clipboard.writeText(item.id)
          toast({ title: "Copied", description: "Skill ID copied" })
        }}
      >
        <Copy className="mr-2 size-3.5" />
        Copy skill ID
      </ContextMenuItem>
      {canDeleteSkill ? (
        <>
          <ContextMenuSeparator />
          <ContextMenuItem
            className="text-xs text-rose-500 focus:text-rose-600"
            onSelect={(event) => {
              event.stopPropagation()
              setSelectedItem(item)
              setActiveDialog("delete")
            }}
          >
            <Trash2 className="mr-2 size-3.5" />
            Delete
          </ContextMenuItem>
        </>
      ) : null}
    </ContextMenuGroup>
  )
}

function SkillCatalogRow({
  item,
  onOpenSkill,
  onOpenFolder,
  setSelectedItem,
  setActiveDialog,
  availableTags,
  areTagsLoading,
  canUpdateSkill,
  canDeleteSkill,
  organizationEnabled,
}: {
  item: SkillDirectoryEntry
  onOpenSkill: (skillId: string) => void
  onOpenFolder: (path: string) => void
  setSelectedItem: (item: SkillDirectoryEntry) => void
  setActiveDialog: (dialog: SkillActiveDialog) => void
  availableTags?: SkillTagRead[]
  areTagsLoading: boolean
  canUpdateSkill: boolean
  canDeleteSkill: boolean
  organizationEnabled: boolean
}) {
  const [contextMenuOpen, setContextMenuOpen] = useState(false)

  if (item.type === "folder") {
    const itemCountLabel = item.num_items === 1 ? "item" : "items"
    return (
      <ContextMenu onOpenChange={setContextMenuOpen}>
        <ContextMenuTrigger asChild>
          <div
            className={cn(
              "group/item flex items-center gap-2 px-4 py-2 transition-colors hover:bg-muted/50",
              contextMenuOpen && "bg-muted/70"
            )}
          >
            <button
              type="button"
              onClick={() => onOpenFolder(item.path)}
              className="flex min-w-0 flex-1 items-center gap-3 bg-transparent p-0 text-left"
            >
              <FolderIcon className="size-4 shrink-0 text-foreground" />
              <div className="flex min-w-0 flex-1 items-center gap-3">
                <span className={ROW_NAME_COLUMN_CLASS}>{item.name}</span>
                <Badge
                  variant="secondary"
                  className="h-5 px-2 text-[10px] font-normal"
                >
                  <BoxIcon className="mr-1 size-3" />
                  {item.num_items} {itemCountLabel}
                </Badge>
                <RowTimestamps
                  className="ml-auto"
                  createdAt={item.created_at}
                  updatedAt={item.updated_at}
                />
              </div>
            </button>
          </div>
        </ContextMenuTrigger>
        <ContextMenuContent className="w-48">
          <SkillFolderContextActions
            item={item}
            setActiveDialog={setActiveDialog}
            setSelectedItem={setSelectedItem}
            canUpdateSkill={canUpdateSkill}
            canDeleteSkill={canDeleteSkill}
          />
        </ContextMenuContent>
      </ContextMenu>
    )
  }

  return (
    <ContextMenu onOpenChange={setContextMenuOpen}>
      <ContextMenuTrigger asChild>
        <div
          className={cn(
            "group/item flex items-center gap-2 px-4 py-3 transition-colors hover:bg-muted/50",
            contextMenuOpen && "bg-muted/70"
          )}
        >
          <button
            type="button"
            onClick={() => onOpenSkill(item.id)}
            className="flex min-w-0 flex-1 items-center gap-3 bg-transparent p-0 text-left"
          >
            <Pyramid className="size-4 shrink-0 text-primary" />
            <div className="flex min-w-0 flex-1 items-center gap-3">
              <span className={ROW_NAME_COLUMN_CLASS}>{item.name}</span>
              <div className="flex min-w-0 flex-1 items-center justify-end gap-2 overflow-hidden">
                <SkillMetadataBadges item={item} />
                {organizationEnabled ? (
                  <SkillTagPills tags={item.tags} />
                ) : null}
              </div>
              <RowTimestamps
                createdAt={item.created_at}
                updatedAt={item.updated_at}
              />
            </div>
          </button>
        </div>
      </ContextMenuTrigger>
      <ContextMenuContent className="w-52">
        <SkillContextActions
          item={item}
          setActiveDialog={setActiveDialog}
          setSelectedItem={setSelectedItem}
          availableTags={availableTags}
          areTagsLoading={areTagsLoading}
          canUpdateSkill={canUpdateSkill}
          canDeleteSkill={canDeleteSkill}
        />
      </ContextMenuContent>
    </ContextMenu>
  )
}

function SkillDeleteFolderDialog({
  open,
  onOpenChange,
  folder,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  folder: SkillFolderDirectoryItem
}) {
  return (
    <FolderNotEmptyDialog
      open={open}
      onOpenChange={onOpenChange}
      folderId={folder.id}
      folderName={folder.name}
      numItems={folder.num_items}
      itemNoun="skills and folders"
    />
  )
}

/**
 * Workspace skills dashboard with optional enterprise folder organization.
 *
 * @param props Workspace identifier.
 * @returns The skills catalog.
 */
export function SkillsDashboard({ workspaceId }: { workspaceId: string }) {
  const router = useRouter()
  const searchParams = useSearchParams()
  const canUpdateSkill = useScopeCheck("agent:update") === true
  const canDeleteSkill = useScopeCheck("agent:delete") === true
  const { hasEntitlement, isLoading: entitlementsLoading } = useEntitlements()
  const organizationEnabled = hasEntitlement("agent_addons")
  const view = organizationEnabled
    ? parseSkillsViewMode(searchParams?.get("view"))
    : "list"
  const currentPath = normalizeSkillFolderPath(searchParams?.get("path"))
  const buildRoute = useCallback(
    (params: URLSearchParams) => {
      const query = params.toString()
      return query
        ? `/workspaces/${workspaceId}/skills?${query}`
        : `/workspaces/${workspaceId}/skills`
    },
    [workspaceId]
  )
  const replaceParams = useCallback(
    (params: URLSearchParams) => router.replace(buildRoute(params)),
    [buildRoute, router]
  )
  const { filter, agentSkillIds, filterIsLoading, filterError } =
    useSkillAgentFilter({
      workspaceId,
      searchParams,
      replaceParams,
      enabled: !entitlementsLoading,
    })
  // A folder only lists its own children, so an active filter searches the
  // flat catalog to reach skills in nested folders.
  const flatCatalog = view === "list" || filter.active
  const { skills, skillsLoading, skillsError } = useSkills(workspaceId, {
    enabled: flatCatalog,
  })
  const { directoryItems, directoryItemsIsLoading, directoryItemsError } =
    useSkillDirectoryItems(currentPath, workspaceId, {
      enabled: organizationEnabled && !flatCatalog,
    })
  const { skillTags, skillTagsIsLoading } = useSkillTagCatalog(workspaceId, {
    enabled: organizationEnabled,
  })
  const { deleteSkill, deleteSkillPending } = useDeleteSkill(workspaceId)

  const [searchQuery, setSearchQuery] = useState("")
  const [sortBy, setSortBy] = useState<SkillsSortValue>(DEFAULT_SKILL_SORT)
  const [limit, setLimit] = useState(20)
  const [page, setPage] = useState(0)
  const [activeDialog, setActiveDialog] = useState<SkillActiveDialog>(null)
  const [selectedItem, setSelectedItem] = useState<SkillDirectoryEntry | null>(
    null
  )
  const [skillToDelete, setSkillToDelete] = useState<SkillReadMinimal | null>(
    null
  )

  const handleViewChange = useCallback(
    (nextView: SkillsViewMode) => {
      const params = new URLSearchParams(searchParams?.toString() ?? "")
      params.set("view", nextView)
      if (nextView === "list") {
        params.delete("path")
      } else if (!params.has("path")) {
        params.set("path", "/")
      }
      replaceParams(params)
    },
    [replaceParams, searchParams]
  )

  const handleOpenFolder = useCallback(
    (path: string) => {
      const params = new URLSearchParams(searchParams?.toString() ?? "")
      params.set("view", "folders")
      params.set("path", normalizeSkillFolderPath(path))
      router.push(buildRoute(params))
    },
    [buildRoute, router, searchParams]
  )

  const handleOpenSkill = useCallback(
    (skillId: string) => {
      router.push(`/workspaces/${workspaceId}/skills/${skillId}`)
    },
    [router, workspaceId]
  )

  const normalizedSearch = searchQuery.trim().toLowerCase()
  const matchesSearch = useCallback(
    (item: SkillDirectoryEntry) => {
      if (!normalizedSearch) return true
      const searchable =
        item.type === "folder"
          ? `${item.name} ${item.path}`
          : `${item.name} ${item.slug} ${item.id} ${item.description ?? ""}`
      return searchable.toLowerCase().includes(normalizedSearch)
    },
    [normalizedSearch]
  )
  const matchesQuery = useCallback(
    (item: SkillDirectoryEntry) =>
      matchesSearch(item) && matchesSkillAgentFilter(item, agentSkillIds),
    [agentSkillIds, matchesSearch]
  )

  const sortedDirectoryItems = useMemo(
    () =>
      [...(directoryItems ?? [])]
        .filter(matchesQuery)
        .sort((a, b) => compareSkillItems(a, b, sortBy)),
    [directoryItems, matchesQuery, sortBy]
  )
  const sortedListItems = useMemo(() => {
    const items: SkillDirectoryEntry[] = (skills ?? []).map((skill) => ({
      type: "skill",
      id: skill.id,
      name: skill.name,
      slug: skill.slug,
      description: skill.description ?? null,
      current_version_id: skill.current_version_id ?? null,
      folder_id: skill.folder_id ?? null,
      tags: skill.tags ?? [],
      created_at: skill.created_at,
      updated_at: skill.updated_at,
    }))
    return items
      .filter(matchesQuery)
      .sort((a, b) => compareSkillItems(a, b, sortBy))
  }, [matchesQuery, skills, sortBy])

  const totalCount = flatCatalog
    ? sortedListItems.length
    : sortedDirectoryItems.length
  const maxPage = flatCatalog
    ? Math.max(Math.ceil(totalCount / limit) - 1, 0)
    : 0
  const safePage = Math.min(page, maxPage)
  const visibleItems = flatCatalog
    ? sortedListItems.slice(safePage * limit, safePage * limit + limit)
    : sortedDirectoryItems
  const isLoading =
    entitlementsLoading ||
    filterIsLoading ||
    (flatCatalog ? skillsLoading : directoryItemsIsLoading)
  const error = (flatCatalog ? skillsError : directoryItemsError) ?? filterError

  const agentFilterKey = filter.agentIds.join(",")
  useEffect(() => {
    setPage(0)
  }, [agentFilterKey, limit, searchQuery, sortBy, view])

  useEffect(() => {
    if (!selectedItem || selectedItem.type !== "skill") return
    if (activeDialog !== "delete") return
    setSkillToDelete({
      id: selectedItem.id,
      workspace_id: workspaceId,
      name: selectedItem.name,
      slug: selectedItem.slug,
      description: selectedItem.description,
      current_version_id: selectedItem.current_version_id,
      folder_id: selectedItem.folder_id,
      tags: selectedItem.tags,
      created_at: selectedItem.created_at,
      updated_at: selectedItem.updated_at,
    })
  }, [activeDialog, selectedItem, workspaceId])

  const handleConfirmDeleteSkill = async () => {
    if (!skillToDelete) return
    try {
      await deleteSkill({ skillId: skillToDelete.id })
      setSkillToDelete(null)
      setSelectedItem(null)
      setActiveDialog(null)
    } catch {
      // The mutation hook reports failures.
    }
  }

  const handleDeleteDialogChange = (open: boolean) => {
    if (!open) {
      setSkillToDelete(null)
      setSelectedItem(null)
      setActiveDialog(null)
    }
  }

  return (
    <div className="flex size-full flex-col overflow-hidden">
      <SkillsHeader
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        sortBy={sortBy}
        onSortByChange={setSortBy}
        agentFilter={filter}
        view={view}
        onViewChange={handleViewChange}
        viewSwitchEnabled={organizationEnabled}
        totalCount={totalCount}
        countLabel="skills"
        limit={limit}
        onLimitChange={setLimit}
        hasPreviousPage={safePage > 0}
        hasNextPage={safePage < maxPage}
        onPreviousPage={() => setPage((current) => Math.max(0, current - 1))}
        onNextPage={() => setPage((current) => Math.min(maxPage, current + 1))}
        isPaginationLoading={isLoading}
      />
      <div className="min-h-0 flex-1 overflow-auto">
        {isLoading ? (
          <div className="flex h-full items-center justify-center">
            <CenteredSpinner />
          </div>
        ) : error ? (
          <div className="flex h-full items-center justify-center px-6">
            <span className="text-sm text-destructive">
              Failed to load skills.
            </span>
          </div>
        ) : visibleItems.length === 0 ? (
          <div className="flex h-full p-6">
            <Empty>
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <Pyramid className="size-5 text-muted-foreground/60" />
                </EmptyMedia>
                <EmptyTitle>
                  {searchQuery.trim() || filter.active
                    ? "No skills match your filters"
                    : "No skills yet"}
                </EmptyTitle>
              </EmptyHeader>
            </Empty>
          </div>
        ) : (
          <div className="divide-y">
            {visibleItems.map((item) => (
              <SkillCatalogRow
                key={`${item.type}-${item.id}`}
                item={item}
                onOpenSkill={handleOpenSkill}
                onOpenFolder={handleOpenFolder}
                setSelectedItem={setSelectedItem}
                setActiveDialog={setActiveDialog}
                availableTags={skillTags}
                areTagsLoading={skillTagsIsLoading}
                canUpdateSkill={canUpdateSkill}
                canDeleteSkill={canDeleteSkill}
                organizationEnabled={organizationEnabled}
              />
            ))}
          </div>
        )}
      </div>

      {organizationEnabled ? (
        <>
          <SkillFolderCreateDialog
            open={activeDialog === "folder-create"}
            onOpenChange={(open) =>
              setActiveDialog(open ? "folder-create" : null)
            }
            currentPath={currentPath}
          />
          {selectedItem?.type === "folder" ? (
            <>
              <SkillFolderRenameDialog
                open={activeDialog === "folder-rename"}
                onOpenChange={(open) =>
                  setActiveDialog(open ? "folder-rename" : null)
                }
                folder={selectedItem}
              />
              <SkillFolderMoveDialog
                open={activeDialog === "folder-move"}
                onOpenChange={(open) =>
                  setActiveDialog(open ? "folder-move" : null)
                }
                folder={selectedItem}
              />
              <SkillDeleteFolderDialog
                open={activeDialog === "delete"}
                onOpenChange={handleDeleteDialogChange}
                folder={selectedItem}
              />
            </>
          ) : null}
          {selectedItem?.type === "skill" ? (
            <SkillMoveDialog
              open={activeDialog === "skill-move"}
              onOpenChange={(open) =>
                setActiveDialog(open ? "skill-move" : null)
              }
              skill={selectedItem}
            />
          ) : null}
        </>
      ) : null}
      <DeleteSkillDialog
        open={activeDialog === "delete" && skillToDelete !== null}
        onOpenChange={handleDeleteDialogChange}
        skill={skillToDelete}
        pending={deleteSkillPending}
        onConfirm={handleConfirmDeleteSkill}
      />
    </div>
  )
}
