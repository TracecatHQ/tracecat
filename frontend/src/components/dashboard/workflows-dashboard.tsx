"use client"

import { format, formatDistanceToNow } from "date-fns"
import {
  AtSignIcon,
  CircleCheck,
  CircleDot,
  Clock3,
  FolderIcon,
  HistoryIcon,
  WorkflowIcon,
} from "lucide-react"
import { useRouter, useSearchParams } from "next/navigation"
import { useCallback, useEffect, useMemo, useState } from "react"
import type {
  FolderDirectoryItem,
  TagRead,
  WorkflowDirectoryItem,
  WorkflowReadMinimal,
} from "@/client"
import { DeleteWorkflowAlertDialog } from "@/components/dashboard/delete-workflow-dialog"
import { FolderDeleteAlertDialog } from "@/components/dashboard/folder-delete-dialog"
import { FolderMoveDialog } from "@/components/dashboard/folder-move-dialog"
import { FolderRenameDialog } from "@/components/dashboard/folder-rename-dialog"
import {
  FolderActions,
  WorkflowActions,
} from "@/components/dashboard/table-actions"
import { ActiveDialog } from "@/components/dashboard/table-common"
import { WorkflowMoveDialog } from "@/components/dashboard/workflow-move-dialog"
import {
  DEFAULT_WORKFLOW_DATE_FILTER,
  isDateFilterActive,
  type WorkflowsDateFilterValue,
  type WorkflowsDatePreset,
  WorkflowsHeader,
  type WorkflowsViewMode,
} from "@/components/dashboard/workflows-header"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Badge } from "@/components/ui/badge"
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuTrigger,
} from "@/components/ui/context-menu"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useWorkflowsPagination } from "@/hooks/pagination/use-workflows-pagination"
import {
  type DirectoryItem,
  useGetDirectoryItems,
  useWorkflowTags,
} from "@/lib/hooks"
import { capitalizeFirst, cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

const DEFAULT_LIMIT = 10

type DateBounds = {
  start: Date | null
  end: Date | null
}

function parseWorkflowsViewMode(value: string | null): WorkflowsViewMode {
  return value === "list" ? "list" : "folders"
}

function normalizeFolderPath(rawPath: string | null): string {
  if (!rawPath || rawPath === "/") {
    return "/"
  }

  const withLeadingSlash = rawPath.startsWith("/") ? rawPath : `/${rawPath}`
  return withLeadingSlash.endsWith("/") && withLeadingSlash !== "/"
    ? withLeadingSlash.slice(0, -1)
    : withLeadingSlash
}

function getDateFromPreset(preset: WorkflowsDatePreset): Date | null {
  if (typeof preset !== "string") {
    return null
  }

  const now = new Date()
  switch (preset) {
    case "1d":
      return new Date(now.getTime() - 24 * 60 * 60 * 1000)
    case "3d":
      return new Date(now.getTime() - 3 * 24 * 60 * 60 * 1000)
    case "1w":
      return new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000)
    case "1m":
      return new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000)
    default:
      return null
  }
}

function getDateBoundsFromFilter(filter: WorkflowsDateFilterValue): DateBounds {
  if (filter.type === "preset") {
    return {
      start: getDateFromPreset(filter.value),
      end: null,
    }
  }

  return {
    start: filter.value.from ?? null,
    end: filter.value.to ?? null,
  }
}

function toEndOfDay(date: Date): Date {
  const end = new Date(date)
  end.setHours(23, 59, 59, 999)
  return end
}

function matchesDateFilter(
  dateValue: string,
  filter: WorkflowsDateFilterValue
): boolean {
  const date = new Date(dateValue)
  if (Number.isNaN(date.getTime())) {
    return false
  }

  const bounds = getDateBoundsFromFilter(filter)
  if (bounds.start && date < bounds.start) {
    return false
  }

  if (bounds.end && date > toEndOfDay(bounds.end)) {
    return false
  }

  return true
}

function getRelativeDateLabel(dateValue: string): string {
  return capitalizeFirst(
    formatDistanceToNow(new Date(dateValue), {
      addSuffix: true,
    })
  )
}

function WorkflowTagPills({ tags }: { tags?: TagRead[] | null }) {
  if (!tags || tags.length === 0) {
    return null
  }

  return (
    <div className="flex shrink-0 items-center gap-1">
      {tags.slice(0, 3).map((tag) => (
        <span
          key={tag.id}
          className={cn(
            "inline-flex h-5 items-center rounded-full px-2 text-[10px] font-medium",
            !tag.color && "bg-muted text-muted-foreground"
          )}
          style={
            tag.color
              ? {
                  backgroundColor: `${tag.color}20`,
                  color: tag.color,
                }
              : undefined
          }
        >
          {tag.name}
        </span>
      ))}
      {tags.length > 3 && (
        <span className="text-[10px] text-muted-foreground">
          +{tags.length - 3}
        </span>
      )}
    </div>
  )
}

function WorkflowMetadataBadges({ item }: { item: WorkflowDirectoryItem }) {
  const lastPublished = item.latest_definition?.created_at ?? null
  const version = item.latest_definition?.version ?? null

  return (
    <div className="flex shrink-0 items-center gap-1">
      {item.alias && (
        <Badge variant="secondary" className="h-5 px-2 text-[10px] font-normal">
          <AtSignIcon className="mr-1 size-3" />
          {item.alias}
        </Badge>
      )}

      <Tooltip>
        <TooltipTrigger asChild>
          <Badge
            variant="secondary"
            className="h-5 cursor-default px-2 text-[10px] font-normal"
          >
            <Clock3 className="mr-1 size-3" />
            {getRelativeDateLabel(item.updated_at)}
          </Badge>
        </TooltipTrigger>
        <TooltipContent>
          {format(new Date(item.updated_at), "PPpp")}
        </TooltipContent>
      </Tooltip>

      {lastPublished ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <Badge
              variant="secondary"
              className="h-5 cursor-default px-2 text-[10px] font-normal"
            >
              <CircleCheck className="mr-1 size-3" />
              {getRelativeDateLabel(lastPublished)}
            </Badge>
          </TooltipTrigger>
          <TooltipContent>
            {format(new Date(lastPublished), "PPpp")}
          </TooltipContent>
        </Tooltip>
      ) : (
        <Badge variant="secondary" className="h-5 px-2 text-[10px] font-normal">
          <CircleDot className="mr-1 size-3" />
          Unpublished
        </Badge>
      )}

      {version !== null && (
        <Badge variant="secondary" className="h-5 px-2 text-[10px] font-normal">
          <HistoryIcon className="mr-1 size-3" />v{version}
        </Badge>
      )}
    </div>
  )
}

function FolderMetadataBadges({ item }: { item: FolderDirectoryItem }) {
  return (
    <div className="flex shrink-0 items-center gap-1">
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge
            variant="secondary"
            className="h-5 cursor-default px-2 text-[10px] font-normal"
          >
            <Clock3 className="mr-1 size-3" />
            {getRelativeDateLabel(item.updated_at)}
          </Badge>
        </TooltipTrigger>
        <TooltipContent>
          {format(new Date(item.updated_at), "PPpp")}
        </TooltipContent>
      </Tooltip>

      <Badge variant="secondary" className="h-5 px-2 text-[10px] font-normal">
        <WorkflowIcon className="mr-1 size-3" />
        {item.num_items} workflows
      </Badge>
    </div>
  )
}

function WorkflowsListRow({
  item,
  onOpenWorkflow,
  onOpenFolder,
  setSelectedWorkflow,
  setSelectedFolder,
  setActiveDialog,
  availableTags,
}: {
  item: DirectoryItem
  onOpenWorkflow: (workflowId: string) => void
  onOpenFolder: (path: string) => void
  setSelectedWorkflow: (workflow: WorkflowReadMinimal | null) => void
  setSelectedFolder: (folder: FolderDirectoryItem | null) => void
  setActiveDialog: (activeDialog: ActiveDialog | null) => void
  availableTags?: TagRead[]
}) {
  const [isContextMenuOpen, setIsContextMenuOpen] = useState(false)

  if (item.type === "folder") {
    return (
      <ContextMenu onOpenChange={setIsContextMenuOpen}>
        <ContextMenuTrigger asChild>
          <div
            className={cn(
              "group/item flex items-center gap-2 px-4 py-2 transition-colors hover:bg-muted/50",
              isContextMenuOpen && "bg-muted/70"
            )}
          >
            <button
              type="button"
              onClick={() => onOpenFolder(item.path)}
              className="flex min-w-0 flex-1 items-center gap-3 bg-transparent p-0 text-left"
            >
              <FolderIcon className="size-4 shrink-0 text-sky-500" />
              <div className="flex min-w-0 flex-1 items-center gap-3">
                <span className="min-w-0 max-w-[340px] flex-1 truncate text-xs">
                  {item.name}
                </span>
                <FolderMetadataBadges item={item} />
              </div>
            </button>
          </div>
        </ContextMenuTrigger>
        <ContextMenuContent className="w-48">
          <FolderActions
            item={item}
            setActiveDialog={setActiveDialog}
            setSelectedFolder={setSelectedFolder}
          />
        </ContextMenuContent>
      </ContextMenu>
    )
  }

  return (
    <ContextMenu onOpenChange={setIsContextMenuOpen}>
      <ContextMenuTrigger asChild>
        <div
          className={cn(
            "group/item flex items-center gap-2 px-4 py-2 transition-colors hover:bg-muted/50",
            isContextMenuOpen && "bg-muted/70"
          )}
        >
          <button
            type="button"
            onClick={() => onOpenWorkflow(item.id)}
            className="flex min-w-0 flex-1 items-center gap-3 bg-transparent p-0 text-left"
          >
            <WorkflowIcon className="size-4 shrink-0 text-orange-500" />
            <div className="flex min-w-0 flex-1 items-center gap-3">
              <span className="min-w-0 max-w-[340px] flex-1 truncate text-xs">
                {item.title}
              </span>
              <WorkflowMetadataBadges item={item} />
              <WorkflowTagPills tags={item.tags} />
            </div>
          </button>
        </div>
      </ContextMenuTrigger>
      <ContextMenuContent className="w-52">
        {/* Reuse the existing workflow action set, now rendered in right-click menu */}
        <WorkflowActions
          item={item}
          availableTags={availableTags}
          showMoveToFolder
          setSelectedWorkflow={setSelectedWorkflow}
          setActiveDialog={setActiveDialog}
        />
      </ContextMenuContent>
    </ContextMenu>
  )
}

export function WorkflowsDashboard() {
  const router = useRouter()
  const workspaceId = useWorkspaceId()
  const searchParams = useSearchParams()

  const view = parseWorkflowsViewMode(searchParams?.get("view"))
  const currentPath = normalizeFolderPath(searchParams?.get("path"))

  const [searchQuery, setSearchQuery] = useState("")
  const [tagFilter, setTagFilter] = useState<string[]>([])
  const [updatedAfter, setUpdatedAfter] = useState<WorkflowsDateFilterValue>(
    DEFAULT_WORKFLOW_DATE_FILTER
  )
  const [createdAfter, setCreatedAfter] = useState<WorkflowsDateFilterValue>(
    DEFAULT_WORKFLOW_DATE_FILTER
  )
  const [limit, setLimit] = useState(DEFAULT_LIMIT)
  const [folderPage, setFolderPage] = useState(0)

  const [activeDialog, setActiveDialog] = useState<ActiveDialog | null>(null)
  const [selectedWorkflow, setSelectedWorkflow] =
    useState<WorkflowReadMinimal | null>(null)
  const [selectedFolder, setSelectedFolder] =
    useState<FolderDirectoryItem | null>(null)

  const { tags } = useWorkflowTags(workspaceId)

  const tagNameByRef = useMemo(() => {
    const map = new Map<string, string>()
    for (const tag of tags ?? []) {
      map.set(tag.ref, tag.name)
    }
    return map
  }, [tags])

  const workflowTagNames = useMemo(
    () =>
      tagFilter
        .map((ref) => tagNameByRef.get(ref))
        .filter((name): name is string => Boolean(name)),
    [tagFilter, tagNameByRef]
  )

  const workflowPagination = useWorkflowsPagination({
    workspaceId,
    limit,
    tags: workflowTagNames,
    enabled: view === "list",
  })

  const { directoryItems, directoryItemsError, directoryItemsIsLoading } =
    useGetDirectoryItems(currentPath, workspaceId, {
      enabled: view === "folders",
    })

  const baseRoute = `/workspaces/${workspaceId}/workflows`

  const buildRoute = (params: URLSearchParams): string => {
    const query = params.toString()
    if (!query) {
      return baseRoute
    }
    return `${baseRoute}?${query}`
  }

  const handleViewChange = (nextView: WorkflowsViewMode) => {
    const nextParams = new URLSearchParams(searchParams?.toString() ?? "")
    nextParams.set("view", nextView)

    if (nextView === "list") {
      nextParams.delete("path")
    } else if (!nextParams.has("path")) {
      nextParams.set("path", "/")
    }

    router.replace(buildRoute(nextParams))
  }

  const handleOpenFolder = (path: string) => {
    const nextParams = new URLSearchParams(searchParams?.toString() ?? "")
    nextParams.set("view", "folders")
    nextParams.set("path", normalizeFolderPath(path))
    router.push(buildRoute(nextParams))
  }

  const tagFilterSet = useMemo(() => new Set(tagFilter), [tagFilter])
  const normalizedSearch = useMemo(
    () => searchQuery.trim().toLowerCase(),
    [searchQuery]
  )

  const matchesFilters = useCallback(
    (item: DirectoryItem): boolean => {
      if (normalizedSearch) {
        const searchable =
          item.type === "workflow"
            ? `${item.title} ${item.alias ?? ""} ${item.id}`
            : `${item.name} ${item.path}`

        if (!searchable.toLowerCase().includes(normalizedSearch)) {
          return false
        }
      }

      if (tagFilterSet.size > 0) {
        if (item.type !== "workflow") {
          return false
        }
        const hasMatchingTag = (item.tags ?? []).some((tag) =>
          tagFilterSet.has(tag.ref)
        )
        if (!hasMatchingTag) {
          return false
        }
      }

      if (!matchesDateFilter(item.updated_at, updatedAfter)) {
        return false
      }

      if (!matchesDateFilter(item.created_at, createdAfter)) {
        return false
      }

      return true
    },
    [normalizedSearch, tagFilterSet, updatedAfter, createdAfter]
  )

  const listItems = useMemo<WorkflowDirectoryItem[]>(
    () =>
      workflowPagination.data.map((workflow) => ({
        ...workflow,
        type: "workflow",
      })),
    [workflowPagination.data]
  )

  const filteredListItems = useMemo(
    () => listItems.filter((item) => matchesFilters(item)),
    [listItems, matchesFilters]
  )

  const filteredDirectoryItems = useMemo(
    () => (directoryItems ?? []).filter((item) => matchesFilters(item)),
    [directoryItems, matchesFilters]
  )

  useEffect(() => {
    setFolderPage(0)
  }, [
    view,
    limit,
    currentPath,
    normalizedSearch,
    tagFilter,
    updatedAfter,
    createdAfter,
  ])

  const folderStartIndex = folderPage * limit
  const folderVisibleItems = useMemo(
    () =>
      filteredDirectoryItems.slice(folderStartIndex, folderStartIndex + limit),
    [filteredDirectoryItems, folderStartIndex, limit]
  )

  const localListFiltersActive =
    normalizedSearch.length > 0 ||
    isDateFilterActive(updatedAfter) ||
    isDateFilterActive(createdAfter)

  const headerTotalCount =
    view === "folders"
      ? filteredDirectoryItems.length
      : localListFiltersActive
        ? filteredListItems.length
        : workflowPagination.totalEstimate || filteredListItems.length

  const visibleItems =
    view === "folders" ? folderVisibleItems : filteredListItems
  const isLoading =
    view === "folders" ? directoryItemsIsLoading : workflowPagination.isLoading
  const error =
    view === "folders" ? directoryItemsError : workflowPagination.error

  const hasPreviousPage =
    view === "folders" ? folderPage > 0 : workflowPagination.hasPreviousPage

  const hasNextPage =
    view === "folders"
      ? folderStartIndex + limit < filteredDirectoryItems.length
      : workflowPagination.hasNextPage

  const handlePreviousPage = () => {
    if (view === "folders") {
      setFolderPage((current) => Math.max(current - 1, 0))
      return
    }
    workflowPagination.goToPreviousPage()
  }

  const handleNextPage = () => {
    if (view === "folders") {
      setFolderPage((current) => {
        const maxPage = Math.max(
          Math.ceil(filteredDirectoryItems.length / limit) - 1,
          0
        )
        return Math.min(current + 1, maxPage)
      })
      return
    }
    workflowPagination.goToNextPage()
  }

  const emptyMessage =
    view === "folders"
      ? "No items found in this folder."
      : "No workflows found."

  return (
    <DeleteWorkflowAlertDialog
      selectedWorkflow={selectedWorkflow}
      setSelectedWorkflow={setSelectedWorkflow}
    >
      <TooltipProvider>
        <div className="flex size-full flex-col overflow-hidden">
          <WorkflowsHeader
            searchQuery={searchQuery}
            onSearchChange={setSearchQuery}
            view={view}
            onViewChange={handleViewChange}
            tags={tags}
            tagFilter={tagFilter}
            onTagChange={setTagFilter}
            updatedAfter={updatedAfter}
            onUpdatedAfterChange={setUpdatedAfter}
            createdAfter={createdAfter}
            onCreatedAfterChange={setCreatedAfter}
            totalCount={headerTotalCount}
            countLabel="workflows"
            limit={limit}
            onLimitChange={setLimit}
            hasPreviousPage={hasPreviousPage}
            hasNextPage={hasNextPage}
            onPreviousPage={handlePreviousPage}
            onNextPage={handleNextPage}
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
                  Failed to load workflows.
                </span>
              </div>
            ) : visibleItems.length === 0 ? (
              <div className="flex h-full items-center justify-center px-6">
                <span className="text-sm text-muted-foreground">
                  {emptyMessage}
                </span>
              </div>
            ) : (
              <div className="divide-y">
                {visibleItems.map((item) => (
                  <WorkflowsListRow
                    key={`${item.type}-${item.id}`}
                    item={item}
                    availableTags={tags}
                    onOpenWorkflow={(workflowId) => {
                      router.push(
                        `/workspaces/${workspaceId}/workflows/${workflowId}`
                      )
                    }}
                    onOpenFolder={handleOpenFolder}
                    setSelectedWorkflow={setSelectedWorkflow}
                    setSelectedFolder={setSelectedFolder}
                    setActiveDialog={setActiveDialog}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      </TooltipProvider>

      <FolderDeleteAlertDialog
        open={activeDialog === ActiveDialog.FolderDelete}
        onOpenChange={() => setActiveDialog(null)}
        selectedFolder={selectedFolder}
        setSelectedFolder={setSelectedFolder}
      />
      <FolderRenameDialog
        open={activeDialog === ActiveDialog.FolderRename}
        onOpenChange={() => setActiveDialog(null)}
        selectedFolder={selectedFolder}
        setSelectedFolder={setSelectedFolder}
      />
      <WorkflowMoveDialog
        open={activeDialog === ActiveDialog.WorkflowMove}
        onOpenChange={() => setActiveDialog(null)}
        selectedWorkflow={selectedWorkflow}
        setSelectedWorkflow={setSelectedWorkflow}
      />
      <FolderMoveDialog
        open={activeDialog === ActiveDialog.FolderMove}
        onOpenChange={() => setActiveDialog(null)}
        selectedFolder={selectedFolder}
        setSelectedFolder={setSelectedFolder}
      />
    </DeleteWorkflowAlertDialog>
  )
}
