"use client"

import { useQueryClient } from "@tanstack/react-query"
import { formatDistanceToNow } from "date-fns"
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ClockPlus,
  FileUpIcon,
  Flag,
  Flame,
  Key,
  PanelRight,
  PenLine,
  Plus,
  Search,
  Sparkles,
  TagsIcon,
  Trash2,
  User,
  X,
} from "lucide-react"
import Link from "next/link"
import { usePathname, useSearchParams } from "next/navigation"
import { Fragment, type ReactNode, useCallback, useState } from "react"
import { type CaseStatus, casesAddTag, type OAuthGrantType } from "@/client"
import { AddCaseDuration } from "@/components/cases/add-case-duration"
import { AddCaseTag } from "@/components/cases/add-case-tag"
import { AddCustomField } from "@/components/cases/add-custom-field"
import {
  PRIORITIES,
  SEVERITIES,
  STATUSES,
} from "@/components/cases/case-categories"
import { CreateCaseDialog } from "@/components/cases/case-create-dialog"
import {
  StatusSelect,
  UNASSIGNED,
} from "@/components/cases/case-panel-selectors"
import { useCaseSelection } from "@/components/cases/case-selection-context"
import {
  CasesViewMode,
  CasesViewToggle,
} from "@/components/cases/cases-view-toggle"
import { CreateWorkflowButton } from "@/components/dashboard/create-workflow-button"
import {
  FolderViewToggle,
  ViewMode,
} from "@/components/dashboard/folder-view-toggle"
import { CreateCustomProviderDialog } from "@/components/integrations/create-custom-provider-dialog"
import { MCPIntegrationDialog } from "@/components/integrations/mcp-integration-dialog"
import { Spinner } from "@/components/loading/spinner"
import { CreateTableDialog } from "@/components/tables/table-create-dialog"
import { TableImportTableDialog } from "@/components/tables/table-import-table-dialog"
import { TableInsertButton } from "@/components/tables/table-insert-button"
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
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import { ButtonGroup, ButtonGroupText } from "@/components/ui/button-group"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { SidebarTrigger } from "@/components/ui/sidebar"
import { toast } from "@/components/ui/use-toast"
import { AddWorkspaceMember } from "@/components/workspaces/add-workspace-member"
import {
  NewCredentialsDialog,
  NewCredentialsDialogTrigger,
} from "@/components/workspaces/add-workspace-secret"
import {
  NewVariableDialog,
  NewVariableDialogTrigger,
} from "@/components/workspaces/add-workspace-variable"
import { useAgentPreset } from "@/hooks/use-agent-presets"
import { useLocalStorage } from "@/hooks/use-local-storage"
import { useWorkspaceDetails, useWorkspaceMembers } from "@/hooks/use-workspace"
import { getDisplayName } from "@/lib/auth"
import {
  useCaseTagCatalog,
  useGetCase,
  useGetTable,
  useIntegrationProvider,
  useUpdateCase,
} from "@/lib/hooks"
import { capitalizeFirst, cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

interface PageConfig {
  title: string | ReactNode
  actions?: ReactNode
}

interface ControlsHeaderProps {
  /** Whether the right-hand chat sidebar is currently open */
  isChatOpen?: boolean
  /** Callback to toggle the chat sidebar */
  onToggleChat?: () => void
}

const CASE_STATUS_TINTS: Record<CaseStatus, string> = {
  new: "bg-yellow-500/[0.03] dark:bg-yellow-500/[0.08]",
  in_progress: "bg-blue-500/[0.03] dark:bg-blue-500/[0.08]",
  on_hold: "bg-orange-500/[0.03] dark:bg-orange-500/[0.08]",
  resolved: "bg-green-500/[0.03] dark:bg-green-500/[0.08]",
  closed: "bg-violet-500/[0.03] dark:bg-violet-500/[0.08]",
  other: "bg-muted/5 dark:bg-muted/[0.12]",
  unknown: "bg-slate-500/[0.03] dark:bg-slate-500/[0.08]",
}

function WorkflowsActions() {
  const searchParams = useSearchParams()
  const currentPath = searchParams?.get("path") || null
  const [view, setView] = useLocalStorage("folder-view", ViewMode.Tags)

  return (
    <>
      <FolderViewToggle view={view} onViewChange={setView} />
      <CreateWorkflowButton
        view={view === ViewMode.Folders ? "folders" : "default"}
        currentFolderPath={currentPath}
      />
    </>
  )
}

function WorkflowsBreadcrumb({
  workspaceId,
  path,
}: {
  workspaceId: string
  path: string | null
}) {
  const normalizePath = (folderPath: string | null) => {
    if (!folderPath || folderPath === "/") return "/"
    const pathWithLeadingSlash = folderPath.startsWith("/")
      ? folderPath
      : `/${folderPath}`
    return pathWithLeadingSlash.endsWith("/") && pathWithLeadingSlash !== "/"
      ? pathWithLeadingSlash.slice(0, -1)
      : pathWithLeadingSlash
  }

  const normalizedPath = normalizePath(path)
  const segments = normalizedPath.split("/").filter(Boolean)
  const baseHref = `/workspaces/${workspaceId}/workflows`
  const getFolderHref = (folderPath: string) => {
    if (folderPath === "/") return baseHref
    return `${baseHref}?path=${encodeURIComponent(folderPath)}`
  }

  return (
    <Breadcrumb>
      <BreadcrumbList className="relative z-10 flex items-center gap-2 text-sm flex-nowrap overflow-hidden whitespace-nowrap min-w-0 bg-transparent pr-1">
        <BreadcrumbItem>
          <BreadcrumbLink asChild className="font-semibold hover:no-underline">
            <Link href={baseHref}>Workflows</Link>
          </BreadcrumbLink>
        </BreadcrumbItem>
        {segments.map((segment, index) => {
          const folderPath = `/${segments.slice(0, index + 1).join("/")}`
          const isLast = index === segments.length - 1
          return (
            <Fragment key={folderPath}>
              <BreadcrumbSeparator className="shrink-0">
                <span className="text-muted-foreground">/</span>
              </BreadcrumbSeparator>
              <BreadcrumbItem>
                {isLast ? (
                  <BreadcrumbPage className="font-semibold">
                    {segment}
                  </BreadcrumbPage>
                ) : (
                  <BreadcrumbLink
                    asChild
                    className="font-semibold hover:no-underline"
                  >
                    <Link href={getFolderHref(folderPath)}>{segment}</Link>
                  </BreadcrumbLink>
                )}
              </BreadcrumbItem>
            </Fragment>
          )
        })}
      </BreadcrumbList>
    </Breadcrumb>
  )
}

function TablesActions() {
  const [activeDialog, setActiveDialog] = useState<"create" | "import" | null>(
    null
  )

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" className="h-7 bg-white">
            <Plus className="mr-1 h-3.5 w-3.5" />
            New table
            <ChevronDown className="ml-1 h-3.5 w-3.5" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="end"
          className="
            [&_[data-radix-collection-item]]:flex
            [&_[data-radix-collection-item]]:items-center
            [&_[data-radix-collection-item]]:gap-2
          "
        >
          <DropdownMenuItem onSelect={() => setActiveDialog("create")}>
            <Plus className="size-4 text-foreground/80" />
            <div className="flex flex-col text-xs">
              <span>Create table</span>
              <span className="text-xs text-muted-foreground">
                Define columns manually
              </span>
            </div>
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => setActiveDialog("import")}>
            <FileUpIcon className="size-4 text-foreground/80" />
            <div className="flex flex-col text-xs">
              <span>Import from CSV</span>
              <span className="text-xs text-muted-foreground">
                Infer columns and data from a CSV file
              </span>
            </div>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <CreateTableDialog
        open={activeDialog === "create"}
        onOpenChange={(open) => setActiveDialog(open ? "create" : null)}
      />
      <TableImportTableDialog
        open={activeDialog === "import"}
        onOpenChange={(open) => setActiveDialog(open ? "import" : null)}
      />
    </>
  )
}

function IntegrationsActions() {
  const [activeDialog, setActiveDialog] = useState<"oauth" | "mcp" | null>(null)

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" className="h-7 bg-white">
            <Plus className="mr-1 h-3.5 w-3.5" />
            Add integration
            <ChevronDown className="ml-1 h-3.5 w-3.5" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="end"
          className="
            [&_[data-radix-collection-item]]:flex
            [&_[data-radix-collection-item]]:items-center
            [&_[data-radix-collection-item]]:gap-2
          "
        >
          <DropdownMenuItem onSelect={() => setActiveDialog("oauth")}>
            <Key className="size-4 text-foreground/80" />
            <div className="flex flex-col text-xs">
              <span>OAuth provider</span>
              <span className="text-xs text-muted-foreground">
                Add a custom OAuth 2.0 provider
              </span>
            </div>
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => setActiveDialog("mcp")}>
            <Sparkles className="size-4 text-foreground/80" />
            <div className="flex flex-col text-xs">
              <span>MCP integration</span>
              <span className="text-xs text-muted-foreground">
                Connect to an MCP server
              </span>
            </div>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <CreateCustomProviderDialog
        open={activeDialog === "oauth"}
        onOpenChange={(open) => setActiveDialog(open ? "oauth" : null)}
        hideTrigger
      />
      <MCPIntegrationDialog
        open={activeDialog === "mcp"}
        onOpenChange={(open) => setActiveDialog(open ? "mcp" : null)}
        hideTrigger
      />
    </>
  )
}

function AgentsActions() {
  const workspaceId = useWorkspaceId()

  return (
    <Button variant="outline" size="sm" className="h-7 bg-white" asChild>
      <Link href={`/workspaces/${workspaceId}/agents/new`}>
        <Plus className="mr-1 h-3.5 w-3.5" />
        New agent
      </Link>
    </Button>
  )
}

function CasesActions() {
  const pathname = usePathname()
  const workspaceId = useWorkspaceId()
  const [dialogOpen, setDialogOpen] = useState(false)

  const view = pathname?.includes("/cases/custom-fields")
    ? CasesViewMode.CustomFields
    : pathname?.includes("/cases/durations")
      ? CasesViewMode.Durations
      : pathname?.includes("/cases/tags")
        ? CasesViewMode.Tags
        : CasesViewMode.Cases

  const casesHref = workspaceId ? `/workspaces/${workspaceId}/cases` : undefined
  const tagsHref = workspaceId
    ? `/workspaces/${workspaceId}/cases/tags`
    : undefined
  const customFieldsHref = workspaceId
    ? `/workspaces/${workspaceId}/cases/custom-fields`
    : undefined
  const durationsHref = workspaceId
    ? `/workspaces/${workspaceId}/cases/durations`
    : undefined

  return (
    <>
      <CasesViewToggle
        view={view}
        casesHref={casesHref}
        tagsHref={tagsHref}
        customFieldsHref={customFieldsHref}
        durationsHref={durationsHref}
      />
      {view === CasesViewMode.CustomFields ? (
        <AddCustomField />
      ) : view === CasesViewMode.Durations ? (
        <AddCaseDuration />
      ) : view === CasesViewMode.Tags ? (
        <AddCaseTag />
      ) : (
        <>
          <Button
            variant="outline"
            size="sm"
            className="h-7 bg-white"
            onClick={() => setDialogOpen(true)}
          >
            <Plus className="mr-1 h-3.5 w-3.5" />
            Create case
          </Button>
          <CreateCaseDialog open={dialogOpen} onOpenChange={setDialogOpen} />
        </>
      )}
    </>
  )
}

function CasesSelectionActionsBar() {
  const {
    selectedCount,
    selectedCaseIds,
    clearSelection,
    deleteSelected,
    bulkUpdateSelectedCases,
    isDeleting,
    isUpdating,
  } = useCaseSelection()
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false)
  const [selectedTagIds, setSelectedTagIds] = useState<Set<string>>(new Set())
  const [isApplyingTags, setIsApplyingTags] = useState(false)
  const workspaceId = useWorkspaceId()
  const queryClient = useQueryClient()
  const { members, membersLoading } = useWorkspaceMembers(workspaceId)
  const { caseTags, caseTagsIsLoading } = useCaseTagCatalog(workspaceId)

  // All callbacks must be defined before any early returns to satisfy React's rules of hooks
  const handleToggleTagSelection = useCallback((tagId: string) => {
    setSelectedTagIds((prev) => {
      const next = new Set(prev)
      if (next.has(tagId)) {
        next.delete(tagId)
      } else {
        next.add(tagId)
      }
      return next
    })
  }, [])

  const handleApplyTags = useCallback(async () => {
    if (selectedTagIds.size === 0 || selectedCaseIds.length === 0) {
      return
    }

    setIsApplyingTags(true)
    try {
      // Apply each selected tag to each selected case
      const promises = selectedCaseIds.flatMap((caseId) =>
        Array.from(selectedTagIds).map((tagId) =>
          casesAddTag({
            caseId,
            workspaceId,
            requestBody: { tag_id: tagId },
          })
        )
      )

      await Promise.all(promises)

      // Invalidate queries to refresh the data
      await queryClient.invalidateQueries({ queryKey: ["cases"] })

      const tagNames = caseTags
        ?.filter((t) => selectedTagIds.has(t.id))
        .map((t) => t.name)
        .join(", ")

      const caseCount = selectedCaseIds.length
      toast({
        title: "Tags applied",
        description: `Applied ${selectedTagIds.size} tag${selectedTagIds.size === 1 ? "" : "s"} (${tagNames}) to ${caseCount} case${caseCount === 1 ? "" : "s"}.`,
      })

      // Clear selection after applying
      setSelectedTagIds(new Set())
    } catch (error) {
      console.error("Failed to apply tags:", error)
      toast({
        title: "Error",
        description: "Failed to apply some tags. Please try again.",
        variant: "destructive",
      })
    } finally {
      setIsApplyingTags(false)
    }
  }, [selectedTagIds, selectedCaseIds, workspaceId, queryClient, caseTags])

  const statusOptions = Object.values(STATUSES)
  const priorityOptions = Object.values(PRIORITIES)
  const severityOptions = Object.values(SEVERITIES)
  const assigneeOptions = [
    {
      value: UNASSIGNED,
      label: "Unassigned",
    },
    ...(members?.map((member) => ({
      value: member.user_id,
      label: getDisplayName({
        first_name: member.first_name,
        last_name: member.last_name,
        email: member.email,
      }),
    })) ?? []),
  ]

  if (!selectedCount || selectedCount === 0) {
    return null
  }

  const isBusy = Boolean(isDeleting) || Boolean(isUpdating) || isApplyingTags
  const canUpdate = Boolean(bulkUpdateSelectedCases) && !isBusy
  const pluralisedCases = `${selectedCount} case${selectedCount === 1 ? "" : "s"}`
  const canApplyTags = !isBusy && caseTags && caseTags.length > 0

  const handleClearSelection = () => {
    if (isBusy) {
      return
    }
    clearSelection?.()
  }

  const handleDelete = async () => {
    if (!deleteSelected) {
      return
    }
    await deleteSelected()
    setConfirmDeleteOpen(false)
  }

  return (
    <>
      <ButtonGroup className="max-w-full">
        <ButtonGroupText className="h-7 px-4 text-xs">
          <span className="font-medium">{selectedCount}</span>
          <span>selected {selectedCount === 1 ? "case" : "cases"}</span>
          {clearSelection && (
            <button
              type="button"
              onClick={handleClearSelection}
              disabled={isBusy}
              className={cn(
                "ml-1 flex size-5 items-center justify-center rounded-full border border-border text-muted-foreground transition hover:bg-muted",
                isBusy && "cursor-not-allowed opacity-60 hover:bg-transparent"
              )}
              aria-label="Clear case selection"
            >
              <X className="size-3.5" />
            </button>
          )}
        </ButtonGroupText>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              className="h-7 px-3 text-xs font-medium"
              disabled={isBusy || !bulkUpdateSelectedCases}
            >
              {isBusy && <Spinner className="mr-2 size-3" />}
              Actions
              <ChevronDown className="ml-1 size-3" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="center" className="w-44">
            <DropdownMenuSub>
              <DropdownMenuSubTrigger disabled={!canUpdate}>
                <span className="flex items-center gap-2">
                  <Flag className="size-3 text-muted-foreground" aria-hidden />
                  <span>Change status</span>
                </span>
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent className="w-48">
                {statusOptions.map((status) => (
                  <DropdownMenuItem
                    key={status.value}
                    disabled={!canUpdate}
                    className="flex items-center gap-2"
                    onSelect={async () => {
                      if (!bulkUpdateSelectedCases) {
                        return
                      }
                      await bulkUpdateSelectedCases(
                        { status: status.value },
                        {
                          successTitle: `Status set to ${status.label}`,
                          successDescription: `Applied to ${pluralisedCases}.`,
                        }
                      )
                    }}
                  >
                    {status.icon && (
                      <status.icon
                        className="size-3 text-muted-foreground"
                        aria-hidden
                      />
                    )}
                    <span>{status.label}</span>
                  </DropdownMenuItem>
                ))}
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            <DropdownMenuSub>
              <DropdownMenuSubTrigger disabled={!canUpdate}>
                <span className="flex items-center gap-2">
                  <AlertTriangle
                    className="size-3 text-muted-foreground"
                    aria-hidden
                  />
                  <span>Change priority</span>
                </span>
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent className="w-48">
                {priorityOptions.map((priority) => (
                  <DropdownMenuItem
                    key={priority.value}
                    disabled={!canUpdate}
                    className="flex items-center gap-2"
                    onSelect={async () => {
                      if (!bulkUpdateSelectedCases) {
                        return
                      }
                      await bulkUpdateSelectedCases(
                        { priority: priority.value },
                        {
                          successTitle: `Priority set to ${priority.label}`,
                          successDescription: `Applied to ${pluralisedCases}.`,
                        }
                      )
                    }}
                  >
                    {priority.icon && (
                      <priority.icon
                        className="size-3 text-muted-foreground"
                        aria-hidden
                      />
                    )}
                    <span>{priority.label}</span>
                  </DropdownMenuItem>
                ))}
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            <DropdownMenuSub>
              <DropdownMenuSubTrigger disabled={!canUpdate}>
                <span className="flex items-center gap-2">
                  <Flame className="size-3 text-muted-foreground" aria-hidden />
                  <span>Change severity</span>
                </span>
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent className="w-48">
                {severityOptions.map((severity) => (
                  <DropdownMenuItem
                    key={severity.value}
                    disabled={!canUpdate}
                    className="flex items-center gap-2"
                    onSelect={async () => {
                      if (!bulkUpdateSelectedCases) {
                        return
                      }
                      await bulkUpdateSelectedCases(
                        { severity: severity.value },
                        {
                          successTitle: `Severity set to ${severity.label}`,
                          successDescription: `Applied to ${pluralisedCases}.`,
                        }
                      )
                    }}
                  >
                    {severity.icon && (
                      <severity.icon
                        className="size-3 text-muted-foreground"
                        aria-hidden
                      />
                    )}
                    <span>{severity.label}</span>
                  </DropdownMenuItem>
                ))}
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            <DropdownMenuSub>
              <DropdownMenuSubTrigger disabled={!canUpdate}>
                <span className="flex items-center gap-2">
                  <User className="size-3 text-muted-foreground" aria-hidden />
                  <span>Assign to</span>
                </span>
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent className="w-56">
                {membersLoading && (
                  <DropdownMenuItem disabled>
                    <Spinner className="mr-2 size-3" /> Loading assignees...
                  </DropdownMenuItem>
                )}
                {!membersLoading && assigneeOptions.length <= 1 && (
                  <DropdownMenuItem disabled>No members found</DropdownMenuItem>
                )}
                {!membersLoading &&
                  assigneeOptions.map((assignee) => (
                    <DropdownMenuItem
                      key={assignee.value}
                      disabled={!canUpdate}
                      onSelect={async () => {
                        if (!bulkUpdateSelectedCases) {
                          return
                        }
                        const isUnassigned = assignee.value === UNASSIGNED
                        await bulkUpdateSelectedCases(
                          { assignee_id: isUnassigned ? null : assignee.value },
                          {
                            successTitle: isUnassigned
                              ? "Cases unassigned"
                              : `Assigned to ${assignee.label}`,
                            successDescription: `Applied to ${pluralisedCases}.`,
                          }
                        )
                      }}
                    >
                      {assignee.label}
                    </DropdownMenuItem>
                  ))}
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            <DropdownMenuSub>
              <DropdownMenuSubTrigger disabled={!canApplyTags}>
                <span className="flex items-center gap-2">
                  <TagsIcon
                    className="size-3 text-muted-foreground"
                    aria-hidden
                  />
                  <span>Apply tags</span>
                </span>
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent className="w-56">
                {caseTagsIsLoading && (
                  <DropdownMenuItem disabled>
                    <Spinner className="mr-2 size-3" /> Loading tags...
                  </DropdownMenuItem>
                )}
                {!caseTagsIsLoading && (!caseTags || caseTags.length === 0) && (
                  <DropdownMenuItem disabled>
                    No tags available
                  </DropdownMenuItem>
                )}
                {!caseTagsIsLoading && caseTags && caseTags.length > 0 && (
                  <>
                    <div className="max-h-48 overflow-y-auto">
                      {caseTags.map((tag) => {
                        const isSelected = selectedTagIds.has(tag.id)
                        return (
                          <DropdownMenuItem
                            key={tag.id}
                            disabled={isBusy}
                            className="flex items-center gap-2"
                            onSelect={(e) => {
                              e.preventDefault()
                              handleToggleTagSelection(tag.id)
                            }}
                          >
                            <div
                              className={cn(
                                "flex size-4 shrink-0 items-center justify-center rounded-sm border",
                                isSelected
                                  ? "border-primary bg-primary text-primary-foreground"
                                  : "border-muted-foreground/40"
                              )}
                            >
                              {isSelected && (
                                <Check className="size-3" aria-hidden />
                              )}
                            </div>
                            <div
                              className="size-2 shrink-0 rounded-full"
                              style={{
                                backgroundColor: tag.color || undefined,
                              }}
                            />
                            <span className="truncate">{tag.name}</span>
                          </DropdownMenuItem>
                        )
                      })}
                    </div>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      disabled={selectedTagIds.size === 0 || isBusy}
                      className="justify-center font-medium"
                      onSelect={async (e) => {
                        e.preventDefault()
                        await handleApplyTags()
                      }}
                    >
                      {isApplyingTags ? (
                        <>
                          <Spinner className="mr-2 size-3" />
                          Applying...
                        </>
                      ) : (
                        <>
                          Apply{" "}
                          {selectedTagIds.size > 0 &&
                            `(${selectedTagIds.size})`}
                        </>
                      )}
                    </DropdownMenuItem>
                  </>
                )}
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              className="text-destructive focus:text-destructive"
              disabled={!deleteSelected || isBusy}
              onSelect={(event) => {
                event.preventDefault()
                if (deleteSelected) {
                  setConfirmDeleteOpen(true)
                }
              }}
            >
              <span className="flex items-center gap-2">
                <Trash2 className="size-3" aria-hidden />
                <span>Delete</span>
              </span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </ButtonGroup>
      <AlertDialog open={confirmDeleteOpen} onOpenChange={setConfirmDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm deletion</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete {selectedCount} selected
              {selectedCount === 1 ? " case" : " cases"}? This action cannot be
              undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={isDeleting}
              onClick={handleDelete}
            >
              {isDeleting ? (
                <span className="flex items-center">
                  <Spinner className="size-4" />
                  <span className="ml-2">Deleting...</span>
                </span>
              ) : (
                "Delete"
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

function MembersActions() {
  const { workspace } = useWorkspaceDetails()

  if (!workspace) {
    return null
  }

  return <AddWorkspaceMember workspace={workspace} />
}

function CredentialsActions() {
  const workspaceId = useWorkspaceId()
  const pathname = usePathname()
  const isOnCatalogPage = pathname?.includes("/credentials/catalog")

  return (
    <div className="flex items-center gap-2">
      <div className="inline-flex items-center rounded-md border bg-transparent">
        <Link
          href={`/workspaces/${workspaceId}/credentials`}
          className={cn(
            "flex h-7 items-center gap-1.5 rounded-l-sm px-2.5 text-xs font-medium transition-colors",
            !isOnCatalogPage
              ? "bg-background text-accent-foreground"
              : "bg-accent text-muted-foreground hover:bg-muted/50"
          )}
        >
          <Key className="h-3.5 w-3.5" />
          Credentials
        </Link>
        <Link
          href={`/workspaces/${workspaceId}/credentials/catalog`}
          className={cn(
            "flex h-7 items-center gap-1.5 rounded-r-sm px-2.5 text-xs font-medium transition-colors",
            isOnCatalogPage
              ? "bg-background text-accent-foreground"
              : "bg-accent text-muted-foreground hover:bg-muted/50"
          )}
        >
          <Search className="h-3.5 w-3.5" />
          Catalog
        </Link>
      </div>
      <NewCredentialsDialog>
        <NewCredentialsDialogTrigger asChild>
          <Button variant="outline" size="sm" className="h-7 bg-white">
            <Plus className="mr-1 h-3.5 w-3.5" />
            Add credential
          </Button>
        </NewCredentialsDialogTrigger>
      </NewCredentialsDialog>
    </div>
  )
}

function VariablesActions() {
  return (
    <NewVariableDialog>
      <NewVariableDialogTrigger asChild>
        <Button variant="outline" size="sm" className="h-7 bg-white">
          <Plus className="mr-1 h-3.5 w-3.5" />
          Add variable
        </Button>
      </NewVariableDialogTrigger>
    </NewVariableDialog>
  )
}

function CaseBreadcrumb({
  caseId,
  workspaceId,
}: {
  caseId: string
  workspaceId: string
}) {
  const { caseData } = useGetCase({ caseId, workspaceId })

  return (
    <Breadcrumb>
      <BreadcrumbList className="relative z-10 flex items-center gap-2 text-sm flex-nowrap overflow-hidden whitespace-nowrap min-w-0 bg-transparent pr-1">
        <BreadcrumbItem>
          <BreadcrumbLink asChild className="font-semibold hover:no-underline">
            <Link href={`/workspaces/${workspaceId}/cases`}>Cases</Link>
          </BreadcrumbLink>
        </BreadcrumbItem>
        <BreadcrumbSeparator className="shrink-0">
          <span className="text-muted-foreground">/</span>
        </BreadcrumbSeparator>
        <BreadcrumbItem>
          <BreadcrumbPage className="font-semibold">
            {caseData?.short_id || caseId}
          </BreadcrumbPage>
        </BreadcrumbItem>
      </BreadcrumbList>
    </Breadcrumb>
  )
}

function CaseTimestamp({
  caseId,
  workspaceId,
  className,
}: {
  caseId: string
  workspaceId: string
  className?: string
}) {
  const { caseData } = useGetCase({ caseId, workspaceId })

  if (!caseData) {
    return null
  }

  return (
    <div
      className={cn(
        "flex items-center gap-2 text-xs text-muted-foreground min-w-0",
        className
      )}
    >
      <span className="hidden sm:flex items-center gap-1 min-w-0">
        <ClockPlus className="h-3 w-3 flex-shrink-0" />
        <span className="truncate min-w-0">
          {capitalizeFirst(
            formatDistanceToNow(new Date(caseData.created_at), {
              addSuffix: true,
            })
          )}
        </span>
      </span>
      <span className="hidden sm:inline flex-shrink-0">•</span>
      <span className="flex items-center gap-1 min-w-0">
        <PenLine className="h-3 w-3 flex-shrink-0" />
        <span className="truncate min-w-0">
          {capitalizeFirst(
            formatDistanceToNow(new Date(caseData.updated_at), {
              addSuffix: true,
            })
          )}
        </span>
      </span>
    </div>
  )
}

function CaseStatusControl({
  caseId,
  workspaceId,
}: {
  caseId: string
  workspaceId: string
}) {
  const { caseData } = useGetCase({ caseId, workspaceId })
  const { updateCase } = useUpdateCase({
    workspaceId,
    caseId,
  })

  if (!caseData) {
    return null
  }

  const handleStatusChange = (newStatus: CaseStatus) => {
    const updatePromise = updateCase({ status: newStatus })
    updatePromise.catch((error) => {
      console.error("Failed to update case status", error)
    })
  }

  return (
    <StatusSelect status={caseData.status} onValueChange={handleStatusChange} />
  )
}

function TableBreadcrumb({
  tableId,
  workspaceId,
}: {
  tableId: string
  workspaceId: string
}) {
  const { table } = useGetTable({ tableId, workspaceId })

  return (
    <Breadcrumb>
      <BreadcrumbList className="relative z-10 flex items-center gap-2 text-sm flex-nowrap overflow-hidden whitespace-nowrap min-w-0 bg-transparent pr-1">
        <BreadcrumbItem>
          <BreadcrumbLink asChild className="font-semibold hover:no-underline">
            <Link href={`/workspaces/${workspaceId}/tables`}>Tables</Link>
          </BreadcrumbLink>
        </BreadcrumbItem>
        <BreadcrumbSeparator className="shrink-0">
          <span className="text-muted-foreground">/</span>
        </BreadcrumbSeparator>
        <BreadcrumbItem>
          <BreadcrumbPage className="font-semibold">
            {table?.name || tableId}
          </BreadcrumbPage>
        </BreadcrumbItem>
      </BreadcrumbList>
    </Breadcrumb>
  )
}

function TableDetailsActions() {
  return <TableInsertButton />
}

function IntegrationBreadcrumb({
  providerId,
  workspaceId,
  grantType,
}: {
  providerId: string
  workspaceId: string
  grantType: OAuthGrantType
}) {
  const { provider } = useIntegrationProvider({
    providerId,
    workspaceId,
    grantType,
  })

  return (
    <Breadcrumb>
      <BreadcrumbList className="relative z-10 flex items-center gap-2 text-sm flex-nowrap overflow-hidden whitespace-nowrap min-w-0 bg-transparent pr-1">
        <BreadcrumbItem>
          <BreadcrumbLink asChild className="font-semibold hover:no-underline">
            <Link href={`/workspaces/${workspaceId}/integrations`}>
              Integrations
            </Link>
          </BreadcrumbLink>
        </BreadcrumbItem>
        <BreadcrumbSeparator className="shrink-0">
          <span className="text-muted-foreground">/</span>
        </BreadcrumbSeparator>
        <BreadcrumbItem>
          <BreadcrumbPage className="font-semibold">
            {provider?.metadata.name || providerId}
          </BreadcrumbPage>
        </BreadcrumbItem>
      </BreadcrumbList>
    </Breadcrumb>
  )
}

function AgentPresetBreadcrumb({
  presetId,
  workspaceId,
}: {
  presetId: string
  workspaceId: string
}) {
  const { preset } = useAgentPreset(workspaceId, presetId)

  return (
    <Breadcrumb>
      <BreadcrumbList className="relative z-10 flex items-center gap-2 text-sm flex-nowrap overflow-hidden whitespace-nowrap min-w-0 bg-transparent pr-1">
        <BreadcrumbItem>
          <BreadcrumbLink asChild className="font-semibold hover:no-underline">
            <Link href={`/workspaces/${workspaceId}/agents`}>Agents</Link>
          </BreadcrumbLink>
        </BreadcrumbItem>
        <BreadcrumbSeparator className="shrink-0">
          <span className="text-muted-foreground">/</span>
        </BreadcrumbSeparator>
        <BreadcrumbItem>
          <BreadcrumbPage className="font-semibold">
            {preset?.name || presetId}
          </BreadcrumbPage>
        </BreadcrumbItem>
      </BreadcrumbList>
    </Breadcrumb>
  )
}

function getPageConfig(
  pathname: string,
  workspaceId: string,
  searchParams: ReturnType<typeof useSearchParams> | null
): PageConfig | null {
  const basePath = `/workspaces/${workspaceId}`

  // Remove base path to get the page route
  const pagePath = pathname.replace(basePath, "") || "/"

  // Match routes and return appropriate config
  if (pagePath === "/" || pagePath.startsWith("/workflows")) {
    return {
      title: (
        <WorkflowsBreadcrumb
          workspaceId={workspaceId}
          path={searchParams?.get("path") ?? "/"}
        />
      ),
      actions: <WorkflowsActions />,
    }
  }

  if (pagePath.startsWith("/approvals")) {
    return {
      title: "Approvals",
    }
  }

  if (pagePath.startsWith("/agents")) {
    // Check if this is an agent preset detail page
    const agentPresetMatch = pagePath.match(/^\/agents\/([^/]+)$/)
    if (agentPresetMatch) {
      const presetId = agentPresetMatch[1]
      // Don't show breadcrumb for "new" preset - it's the create page
      if (presetId === "new") {
        return {
          title: "Agents",
          actions: <AgentsActions />,
        }
      }
      return {
        title: (
          <AgentPresetBreadcrumb
            presetId={presetId}
            workspaceId={workspaceId}
          />
        ),
      }
    }

    return {
      title: "Agents",
      actions: <AgentsActions />,
    }
  }

  if (pagePath.startsWith("/cases")) {
    if (
      pagePath === "/cases/custom-fields" ||
      pagePath === "/cases/durations" ||
      pagePath === "/cases/tags"
    ) {
      return {
        title: "Cases",
        actions: <CasesActions />,
      }
    }

    // Check if this is a case detail page
    const caseMatch = pagePath.match(/^\/cases\/([^/]+)$/)
    if (caseMatch) {
      const caseId = caseMatch[1]
      return {
        title: <CaseBreadcrumb caseId={caseId} workspaceId={workspaceId} />,
        // No actions for case detail pages
      }
    }

    return {
      title: "Cases",
      actions: <CasesActions />,
    }
  }

  if (pagePath.startsWith("/tables")) {
    // Check if this is a table detail page
    const tableMatch = pagePath.match(/^\/tables\/([^/]+)$/)
    if (tableMatch) {
      const tableId = tableMatch[1]
      return {
        title: <TableBreadcrumb tableId={tableId} workspaceId={workspaceId} />,
        actions: <TableDetailsActions />,
      }
    }

    return {
      title: "Tables",
      actions: <TablesActions />,
    }
  }

  if (pagePath.startsWith("/integrations")) {
    // Check if this is an integration detail page
    const integrationMatch = pagePath.match(/^\/integrations\/([^/]+)$/)
    if (integrationMatch && searchParams) {
      const providerId = integrationMatch[1]
      const grantType = searchParams.get("grant_type") as OAuthGrantType
      if (grantType) {
        return {
          title: (
            <IntegrationBreadcrumb
              providerId={providerId}
              workspaceId={workspaceId}
              grantType={grantType}
            />
          ),
        }
      }
    }

    return {
      title: "Integrations",
      actions: <IntegrationsActions />,
    }
  }

  if (pagePath.startsWith("/credentials")) {
    return {
      title: "Credentials",
      actions: <CredentialsActions />,
    }
  }

  if (pagePath.startsWith("/variables")) {
    return {
      title: "Variables",
      actions: <VariablesActions />,
    }
  }

  if (pagePath.startsWith("/members")) {
    return {
      title: "Members",
      actions: <MembersActions />,
    }
  }

  if (pagePath.startsWith("/inbox")) {
    return {
      title: "Inbox",
    }
  }

  return null
}

export function ControlsHeader({
  isChatOpen,
  onToggleChat,
}: ControlsHeaderProps = {}) {
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const workspaceId = useWorkspaceId()
  const pagePath = pathname
    ? pathname.replace(`/workspaces/${workspaceId}`, "") || "/"
    : "/"
  const isCaseDetail = pagePath.match(
    /^\/cases\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i
  )
  const caseId = isCaseDetail ? isCaseDetail[1] : null

  const pageConfig = pathname
    ? getPageConfig(pathname, workspaceId, searchParams ?? null)
    : null
  const { caseData } = useGetCase(
    { caseId: caseId ?? "", workspaceId },
    { enabled: Boolean(caseId) }
  )

  if (!pageConfig) {
    return null
  }

  // Check if this is a case detail page to show timestamp
  // Only apply background for case detail pages with status tints.
  // Non-case pages should be transparent to avoid painting over SidebarInset's rounded corners.
  const headerBackgroundClass = caseId
    ? caseData?.status
      ? CASE_STATUS_TINTS[caseData.status]
      : "bg-muted/5 dark:bg-muted/[0.12]"
    : ""

  const titleContent =
    typeof pageConfig.title === "string" ? (
      <h1 className="text-sm font-semibold">{pageConfig.title}</h1>
    ) : (
      pageConfig.title
    )

  return (
    <header
      className={cn(
        "flex h-10 items-center border-b px-3 overflow-hidden transition-colors",
        headerBackgroundClass
      )}
    >
      {/* Left section: sidebar toggle + title */}
      <div className="flex items-center gap-3 min-w-0">
        <SidebarTrigger className="h-7 w-7 flex-shrink-0" />
        {caseId ? (
          <div className="flex items-center gap-3 min-w-0">
            <div className="min-w-0">{titleContent}</div>
            <CaseTimestamp
              caseId={caseId}
              workspaceId={workspaceId}
              className="ml-3 pl-3"
            />
          </div>
        ) : (
          titleContent
        )}
      </div>

      {/* Middle section: bulk selection actions */}
      <div className="flex flex-1 justify-center min-w-[1rem]">
        <CasesSelectionActionsBar />
      </div>

      {/* Right section: actions / timestamp / chat toggle */}
      <div className="flex items-center gap-2 flex-shrink-0">
        {pageConfig.actions
          ? pageConfig.actions
          : caseId && (
              <CaseStatusControl caseId={caseId} workspaceId={workspaceId} />
            )}

        {onToggleChat && (
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={onToggleChat}
          >
            <PanelRight className="h-4 w-4 text-muted-foreground" />
            <span className="sr-only">Toggle Chat</span>
          </Button>
        )}
      </div>
    </header>
  )
}
