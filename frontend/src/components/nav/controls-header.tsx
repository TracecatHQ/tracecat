"use client"

import { useQueryClient } from "@tanstack/react-query"
import { formatDistanceToNow } from "date-fns"
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ClockPlus,
  FileText,
  FileUpIcon,
  Flag,
  Flame,
  FolderIcon,
  ListIcon,
  Lock,
  MessageSquare,
  MousePointerClickIcon,
  PanelRight,
  PenLine,
  Plus,
  Sparkles,
  TagsIcon,
  Trash2,
  User,
  X,
} from "lucide-react"
import dynamic from "next/dynamic"
import Link from "next/link"
import { usePathname, useSearchParams } from "next/navigation"
import {
  Fragment,
  type ReactNode,
  useCallback,
  useEffect,
  useState,
} from "react"
import {
  type CaseStatus,
  casesAddTag,
  casesCreateComment,
  casesGetCase,
  casesUpdateCase,
} from "@/client"
import {
  AgentsCatalogViewMode,
  AgentsCatalogViewToggle,
} from "@/components/agents/agents-catalog-view-toggle"
import { AgentFolderCreateDialog } from "@/components/agents/agents-dashboard"
import { CreateAgentDialog } from "@/components/agents/create-agent-dialog"
import { AddCaseDropdown } from "@/components/cases/add-case-dropdown"
import { AddCaseDuration } from "@/components/cases/add-case-duration"
import { AddCaseTag } from "@/components/cases/add-case-tag"
import { AddCustomField } from "@/components/cases/add-custom-field"
import {
  PRIORITIES,
  SEVERITIES,
  STATUSES,
} from "@/components/cases/case-categories"
import { CaseClosureDialog } from "@/components/cases/case-closure-dialog"
import { CreateCaseDialog } from "@/components/cases/case-create-dialog"
import { CaseDurationMetrics } from "@/components/cases/case-duration-metrics"
import { UNASSIGNED } from "@/components/cases/case-panel-selectors"
import { useCaseSelection } from "@/components/cases/case-selection-context"
import {
  CasesViewMode,
  CasesViewToggle,
} from "@/components/cases/cases-view-toggle"
import { AddWorkflowTag } from "@/components/dashboard/add-workflow-tag"
import { CreateWorkflowButton } from "@/components/dashboard/create-workflow-button"
import {
  WorkflowsCatalogViewMode,
  WorkflowsCatalogViewToggle,
} from "@/components/dashboard/workflows-catalog-view-toggle"
import { DynamicLucideIcon } from "@/components/dynamic-lucide-icon"
import { CreateCustomProviderDialog } from "@/components/integrations/create-custom-provider-dialog"
import { MCPIntegrationDialog } from "@/components/integrations/mcp-integration-dialog"
import { Spinner } from "@/components/loading/spinner"
import {
  MembersViewMode,
  MembersViewToggle,
} from "@/components/members/members-view-toggle"
import { CreateGroupButton } from "@/components/rbac/create-group-button"
import { CreateRoleButton } from "@/components/rbac/create-role-button"
import { RegistryActionsControls } from "@/components/registry/workspace-actions-controls"
import { TableSelectionActionsBar } from "@/components/tables/ag-grid-bulk-actions"
import { CreateTableDialog } from "@/components/tables/table-create-dialog"
import { TableImportTableDialog } from "@/components/tables/table-import-table-dialog"
import { TableInsertButton } from "@/components/tables/table-insert-button"
import { TableLinkRowsToCaseCommand } from "@/components/tables/table-link-rows-to-case-command"
import { CreateTagDialog } from "@/components/tags/create-tag-dialog"

const SimpleEditor = dynamic(
  () =>
    import("@/components/tiptap-templates/simple/simple-editor").then(
      (m) => m.SimpleEditor
    ),
  { ssr: false }
)

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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
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
import { Kbd } from "@/components/ui/kbd"
import { SidebarTrigger } from "@/components/ui/sidebar"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { AddWorkspaceMember } from "@/components/workspaces/add-workspace-member"
import {
  NewVariableDialog,
  NewVariableDialogTrigger,
} from "@/components/workspaces/add-workspace-variable"
import { CreateCredentialDialog } from "@/components/workspaces/create-credential-dialog"
import { useAgentPreset, useAgentTagCatalog } from "@/hooks/use-agent-presets"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useWorkspaceDetails, useWorkspaceMembers } from "@/hooks/use-workspace"
import {
  useCaseDropdownDefinitions,
  useCaseDurationDefinitions,
  useCaseDurations,
  useCaseFields,
  useCaseTagCatalog,
  useGetCase,
  useGetTable,
} from "@/lib/hooks"
import { capitalizeFirst, cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

interface PageConfig {
  title: string | ReactNode
  actions?: ReactNode
}

interface ControlsHeaderProps {
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

const CHAT_TOGGLE_KEY = "c"

function WorkflowsActions() {
  const pathname = usePathname()
  const workspaceId = useWorkspaceId()
  const searchParams = useSearchParams()
  const catalogView = pathname?.includes("/workflows/tags")
    ? WorkflowsCatalogViewMode.Tags
    : WorkflowsCatalogViewMode.Workflows
  const view = searchParams?.get("view") === "list" ? "list" : "folders"
  const currentPath =
    view === "folders" ? searchParams?.get("path") || "/" : null
  const workflowsHref = `/workspaces/${workspaceId}/workflows`
  const tagsHref = `/workspaces/${workspaceId}/workflows/tags`

  return (
    <>
      <WorkflowsCatalogViewToggle
        view={catalogView}
        workflowsHref={workflowsHref}
        tagsHref={tagsHref}
      />
      {catalogView === WorkflowsCatalogViewMode.Tags ? (
        <AddWorkflowTag />
      ) : (
        <CreateWorkflowButton
          view={view === "folders" ? "folders" : "default"}
          currentFolderPath={currentPath}
        />
      )}
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
    if (folderPath === "/") return `${baseHref}?view=folders&path=%2F`
    return `${baseHref}?view=folders&path=${encodeURIComponent(folderPath)}`
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
            <Lock className="size-4 text-foreground/80" />
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
  const pathname = usePathname()
  const workspaceId = useWorkspaceId()
  const searchParams = useSearchParams()
  const [createDialogOpen, setCreateDialogOpen] = useState(false)
  const [createTagDialogOpen, setCreateTagDialogOpen] = useState(false)
  const [folderDialogOpen, setFolderDialogOpen] = useState(false)

  const catalogView = pathname?.includes("/agents/tags")
    ? AgentsCatalogViewMode.Tags
    : AgentsCatalogViewMode.Agents
  const agentsHref = `/workspaces/${workspaceId}/agents`
  const tagsHref = `/workspaces/${workspaceId}/agents/tags`
  const isFoldersView = searchParams?.get("view") !== "list"
  const currentPath = searchParams?.get("path") || "/"

  return (
    <>
      <AgentsCatalogViewToggle
        view={catalogView}
        agentsHref={agentsHref}
        tagsHref={tagsHref}
      />
      {catalogView === AgentsCatalogViewMode.Tags ? (
        <AddAgentTag
          open={createTagDialogOpen}
          onOpenChange={setCreateTagDialogOpen}
        />
      ) : (
        <>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm" className="h-7 bg-white">
                <Plus className="mr-1 h-3.5 w-3.5" />
                Create new
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
              <DropdownMenuItem onSelect={() => setCreateDialogOpen(true)}>
                <MousePointerClickIcon className="size-4 text-foreground/80" />
                <div className="flex flex-col text-xs">
                  <span>Agent</span>
                  <span className="text-xs text-muted-foreground">
                    Start from scratch
                  </span>
                </div>
              </DropdownMenuItem>
              {isFoldersView && (
                <DropdownMenuItem onSelect={() => setFolderDialogOpen(true)}>
                  <FolderIcon className="size-4 text-foreground/80" />
                  <div className="flex flex-col text-xs">
                    <span>Folder</span>
                    <span className="text-xs text-muted-foreground">
                      Create a new folder
                    </span>
                  </div>
                </DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
          <CreateAgentDialog
            open={createDialogOpen}
            onOpenChange={setCreateDialogOpen}
          />
          <AgentFolderCreateDialog
            open={folderDialogOpen}
            onOpenChange={setFolderDialogOpen}
            currentPath={currentPath}
          />
        </>
      )}
    </>
  )
}

function AddAgentTag({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const workspaceId = useWorkspaceId()
  const { agentTags, createAgentTag } = useAgentTagCatalog(workspaceId, {
    enabled: open,
  })

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        className="h-7 bg-white"
        onClick={() => onOpenChange(true)}
      >
        <Plus className="mr-1 h-3.5 w-3.5" />
        Create tag
      </Button>
      <CreateTagDialog
        open={open}
        onOpenChange={onOpenChange}
        existingTags={agentTags}
        onCreateTag={async (params) => {
          await createAgentTag(params)
        }}
        title="Create new agent tag"
        description="Enter a name for your new agent tag."
      />
    </>
  )
}

function CasesActions() {
  const pathname = usePathname()
  const workspaceId = useWorkspaceId()
  const [dialogOpen, setDialogOpen] = useState(false)

  const view = pathname?.includes("/cases/custom-fields")
    ? CasesViewMode.CustomFields
    : pathname?.includes("/cases/closure-requirements")
      ? CasesViewMode.ClosureRequirements
      : pathname?.includes("/cases/durations")
        ? CasesViewMode.Durations
        : pathname?.includes("/cases/tags")
          ? CasesViewMode.Tags
          : pathname?.includes("/cases/dropdowns")
            ? CasesViewMode.Dropdowns
            : CasesViewMode.Cases

  const casesHref = workspaceId ? `/workspaces/${workspaceId}/cases` : undefined
  const tagsHref = workspaceId
    ? `/workspaces/${workspaceId}/cases/tags`
    : undefined
  const customFieldsHref = workspaceId
    ? `/workspaces/${workspaceId}/cases/custom-fields`
    : undefined
  const dropdownsHref = workspaceId
    ? `/workspaces/${workspaceId}/cases/dropdowns`
    : undefined
  const closureRequirementsHref = workspaceId
    ? `/workspaces/${workspaceId}/cases/closure-requirements`
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
        dropdownsHref={dropdownsHref}
        closureRequirementsHref={closureRequirementsHref}
        durationsHref={durationsHref}
      />
      {view === CasesViewMode.CustomFields ? (
        <AddCustomField />
      ) : view === CasesViewMode.Durations ? (
        <AddCaseDuration />
      ) : view === CasesViewMode.Tags ? (
        <AddCaseTag />
      ) : view === CasesViewMode.Dropdowns ? (
        <AddCaseDropdown />
      ) : view === CasesViewMode.ClosureRequirements ? null : (
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

function CasesSelectionActionsBar({ enabled = true }: { enabled?: boolean }) {
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
  const [commentDialogOpen, setCommentDialogOpen] = useState(false)
  const [commentText, setCommentText] = useState("")
  const [isAddingComments, setIsAddingComments] = useState(false)
  const [appendDialogOpen, setAppendDialogOpen] = useState(false)
  const [appendText, setAppendText] = useState("")
  const [isAppending, setIsAppending] = useState(false)
  const workspaceId = useWorkspaceId()
  const queryClient = useQueryClient()
  const { hasEntitlement } = useEntitlements()
  const caseAddonsEnabled = hasEntitlement("case_addons")
  const shouldLoadCatalogData = enabled && selectedCount > 0
  const { members, membersLoading } = useWorkspaceMembers(workspaceId, {
    enabled: shouldLoadCatalogData,
  })
  const { caseTags, caseTagsIsLoading } = useCaseTagCatalog(workspaceId, {
    enabled: shouldLoadCatalogData,
  })
  const { dropdownDefinitions, dropdownDefinitionsIsLoading } =
    useCaseDropdownDefinitions(
      workspaceId,
      shouldLoadCatalogData && caseAddonsEnabled
    )
  const { caseFields: caseFieldDefinitions } = useCaseFields(
    workspaceId,
    shouldLoadCatalogData && caseAddonsEnabled
  )
  const [closureDialog, setClosureDialog] = useState<{
    open: boolean
    targetStatus: CaseStatus
  } | null>(null)

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

  const handleBulkAddComment = useCallback(async () => {
    if (!commentText.trim() || selectedCaseIds.length === 0) return

    setIsAddingComments(true)
    try {
      await Promise.all(
        selectedCaseIds.map((caseId) =>
          casesCreateComment({
            caseId,
            workspaceId,
            requestBody: { content: commentText.trim() },
          })
        )
      )

      await queryClient.invalidateQueries({ queryKey: ["cases"] })

      const caseCount = selectedCaseIds.length
      toast({
        title: "Comments added",
        description: `Added comment to ${caseCount} case${caseCount === 1 ? "" : "s"}.`,
      })

      setCommentText("")
      setCommentDialogOpen(false)
    } catch (error) {
      console.error("Failed to add comments:", error)
      toast({
        title: "Error",
        description: "Failed to add comments to some cases. Please try again.",
        variant: "destructive",
      })
    } finally {
      setIsAddingComments(false)
    }
  }, [commentText, selectedCaseIds, workspaceId, queryClient])

  const handleBulkAppendDescription = useCallback(async () => {
    if (!appendText.trim() || selectedCaseIds.length === 0) return

    setIsAppending(true)
    try {
      const cases = await Promise.all(
        selectedCaseIds.map((caseId) => casesGetCase({ caseId, workspaceId }))
      )

      await Promise.all(
        cases.map((c) =>
          casesUpdateCase({
            caseId: c.id,
            workspaceId,
            requestBody: {
              description:
                (c.description ? `${c.description}\n\n` : "") +
                appendText.trim(),
            },
          })
        )
      )

      await queryClient.invalidateQueries({ queryKey: ["cases"] })

      const caseCount = selectedCaseIds.length
      toast({
        title: "Descriptions updated",
        description: `Appended text to ${caseCount} case${caseCount === 1 ? "" : "s"}.`,
      })

      setAppendText("")
      setAppendDialogOpen(false)
    } catch (error) {
      console.error("Failed to append to descriptions:", error)
      toast({
        title: "Error",
        description:
          "Failed to update some case descriptions. Please try again.",
        variant: "destructive",
      })
    } finally {
      setIsAppending(false)
    }
  }, [appendText, selectedCaseIds, workspaceId, queryClient])

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
      label: member.email,
    })) ?? []),
  ]

  if (!selectedCount || selectedCount === 0) {
    return null
  }

  const isBusy =
    Boolean(isDeleting) ||
    Boolean(isUpdating) ||
    isApplyingTags ||
    isAddingComments ||
    isAppending
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
                  <span>Status</span>
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
                      // Intercept closed/resolved for closure requirements
                      if (
                        caseAddonsEnabled &&
                        (status.value === "closed" ||
                          status.value === "resolved")
                      ) {
                        const reqFields =
                          caseFieldDefinitions?.filter(
                            (f) => !f.reserved && f.required_on_closure
                          ) ?? []
                        const reqDropdowns =
                          dropdownDefinitions?.filter(
                            (d) => d.required_on_closure
                          ) ?? []
                        if (reqFields.length > 0 || reqDropdowns.length > 0) {
                          setClosureDialog({
                            open: true,
                            targetStatus: status.value,
                          })
                          return
                        }
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
                  <span>Priority</span>
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
                  <span>Severity</span>
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
            {caseAddonsEnabled && dropdownDefinitionsIsLoading && (
              <DropdownMenuItem disabled>
                <Spinner className="mr-2 size-3" /> Loading dropdowns...
              </DropdownMenuItem>
            )}
            {caseAddonsEnabled &&
              !dropdownDefinitionsIsLoading &&
              dropdownDefinitions?.map((definition) => (
                <DropdownMenuSub key={definition.id}>
                  <DropdownMenuSubTrigger disabled={!canUpdate}>
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="shrink-0">
                        {definition.icon_name ? (
                          <DynamicLucideIcon
                            name={definition.icon_name}
                            className="size-3 text-muted-foreground"
                            fallback={
                              <ListIcon className="size-3 text-muted-foreground" />
                            }
                          />
                        ) : (
                          <ListIcon className="size-3 text-muted-foreground" />
                        )}
                      </span>
                      <span className="truncate" title={definition.name}>
                        {definition.name}
                      </span>
                    </span>
                  </DropdownMenuSubTrigger>
                  <DropdownMenuSubContent className="w-48">
                    <DropdownMenuItem
                      disabled={!canUpdate}
                      className="flex items-center gap-2"
                      onSelect={async () => {
                        if (!bulkUpdateSelectedCases) return
                        await bulkUpdateSelectedCases(
                          {
                            dropdown_values: [
                              {
                                definition_id: definition.id,
                                option_id: null,
                              },
                            ],
                          },
                          {
                            successTitle: `${definition.name} cleared`,
                            successDescription: `Applied to ${pluralisedCases}.`,
                          }
                        )
                      }}
                    >
                      <span className="text-muted-foreground">None</span>
                    </DropdownMenuItem>
                    {definition.options?.map((opt) => {
                      const optionStyle = opt.color
                        ? ({ color: opt.color } as React.CSSProperties)
                        : undefined
                      return (
                        <DropdownMenuItem
                          key={opt.id}
                          disabled={!canUpdate}
                          className="flex items-center gap-2"
                          onSelect={async () => {
                            if (!bulkUpdateSelectedCases) return
                            await bulkUpdateSelectedCases(
                              {
                                dropdown_values: [
                                  {
                                    definition_id: definition.id,
                                    option_id: opt.id,
                                  },
                                ],
                              },
                              {
                                successTitle: `${definition.name} set to ${opt.label}`,
                                successDescription: `Applied to ${pluralisedCases}.`,
                              }
                            )
                          }}
                        >
                          {opt.icon_name ? (
                            <DynamicLucideIcon
                              name={opt.icon_name}
                              className="size-3"
                              style={optionStyle}
                              fallback={
                                <span
                                  className="size-3 shrink-0 rounded-full bg-muted-foreground/40"
                                  style={optionStyle}
                                />
                              }
                            />
                          ) : opt.color ? (
                            <span
                              className="size-3 shrink-0 rounded-full"
                              style={{ backgroundColor: opt.color }}
                            />
                          ) : null}
                          <span style={optionStyle}>{opt.label}</span>
                        </DropdownMenuItem>
                      )
                    })}
                  </DropdownMenuSubContent>
                </DropdownMenuSub>
              ))}
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
              disabled={isBusy}
              onSelect={(event) => {
                event.preventDefault()
                setCommentDialogOpen(true)
              }}
            >
              <span className="flex items-center gap-2">
                <MessageSquare
                  className="size-3 text-muted-foreground"
                  aria-hidden
                />
                <span>Add comment</span>
              </span>
            </DropdownMenuItem>
            <DropdownMenuItem
              disabled={isBusy}
              onSelect={(event) => {
                event.preventDefault()
                setAppendDialogOpen(true)
              }}
            >
              <span className="flex items-center gap-2">
                <FileText
                  className="size-3 text-muted-foreground"
                  aria-hidden
                />
                <span>Append to description</span>
              </span>
            </DropdownMenuItem>
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
      <Dialog
        open={commentDialogOpen}
        onOpenChange={(open) => {
          setCommentDialogOpen(open)
          if (!open) setCommentText("")
        }}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader className="text-left">
            <DialogTitle>Add comment</DialogTitle>
            <DialogDescription>
              Add a comment to {selectedCount} selected
              {selectedCount === 1 ? " case" : " cases"}.
            </DialogDescription>
          </DialogHeader>
          <SimpleEditor
            value={commentText}
            onChange={setCommentText}
            placeholder="Enter comment..."
            className="min-h-[150px]"
          />
          <DialogFooter className="flex-row justify-end space-x-2">
            <Button
              variant="outline"
              onClick={() => {
                setCommentDialogOpen(false)
                setCommentText("")
              }}
            >
              Cancel
            </Button>
            <Button
              disabled={!commentText.trim() || isAddingComments}
              onClick={handleBulkAddComment}
            >
              {isAddingComments ? (
                <>
                  <Spinner className="mr-2 size-3" />
                  Adding...
                </>
              ) : (
                "Add comment"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog
        open={appendDialogOpen}
        onOpenChange={(open) => {
          setAppendDialogOpen(open)
          if (!open) setAppendText("")
        }}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader className="text-left">
            <DialogTitle>Append to description</DialogTitle>
            <DialogDescription>
              Append text to the description of {selectedCount} selected
              {selectedCount === 1 ? " case" : " cases"}.
            </DialogDescription>
          </DialogHeader>
          <SimpleEditor
            value={appendText}
            onChange={setAppendText}
            placeholder="Enter text to append..."
            className="min-h-[150px]"
          />
          <DialogFooter className="flex-row justify-end space-x-2">
            <Button
              variant="outline"
              onClick={() => {
                setAppendDialogOpen(false)
                setAppendText("")
              }}
            >
              Cancel
            </Button>
            <Button
              disabled={!appendText.trim() || isAppending}
              onClick={handleBulkAppendDescription}
            >
              {isAppending ? (
                <>
                  <Spinner className="mr-2 size-3" />
                  Appending...
                </>
              ) : (
                "Append"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {closureDialog && (
        <CaseClosureDialog
          open={closureDialog.open}
          onOpenChange={(open) => {
            if (!open) setClosureDialog(null)
          }}
          targetStatus={closureDialog.targetStatus as "closed" | "resolved"}
          requiredFields={
            caseFieldDefinitions?.filter(
              (f) => !f.reserved && f.required_on_closure
            ) ?? []
          }
          requiredDropdowns={
            dropdownDefinitions?.filter((d) => d.required_on_closure) ?? []
          }
          isBulk
          selectedCount={selectedCount}
          onSubmit={async (data) => {
            if (!bulkUpdateSelectedCases) return
            const statusLabel =
              closureDialog.targetStatus === "closed" ? "Closed" : "Resolved"
            await bulkUpdateSelectedCases(
              {
                status: closureDialog.targetStatus,
                fields: data.fields,
                dropdown_values: data.dropdown_values.map((dv) => ({
                  definition_id: dv.definition_id,
                  option_id: dv.option_id,
                })),
              },
              {
                successTitle: `Status set to ${statusLabel}`,
                successDescription: `Applied to ${pluralisedCases}.`,
              }
            )
          }}
        />
      )}
    </>
  )
}

function MembersActions({ view }: { view: MembersViewMode }) {
  const { workspace } = useWorkspaceDetails()
  const workspaceId = useWorkspaceId()

  if (!workspace) {
    return null
  }

  // Render the appropriate action button based on the current view
  const actionButton =
    view === MembersViewMode.Roles ? (
      <CreateRoleButton workspaceOnly />
    ) : view === MembersViewMode.Groups ? (
      <CreateGroupButton />
    ) : (
      <AddWorkspaceMember workspace={workspace} />
    )

  return (
    <>
      <MembersViewToggle
        view={view}
        membersHref={`/workspaces/${workspaceId}/members`}
        rolesHref={`/workspaces/${workspaceId}/members/roles`}
        groupsHref={`/workspaces/${workspaceId}/members/groups`}
        rbacScope="workspace:rbac:read"
      />
      {actionButton}
    </>
  )
}

function CredentialsActions() {
  const [dialogOpen, setDialogOpen] = useState(false)

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        className="h-7 bg-white"
        onClick={() => setDialogOpen(true)}
      >
        <Plus className="mr-1 h-3.5 w-3.5" />
        Add credential
      </Button>

      <CreateCredentialDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </>
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
  const { hasEntitlement } = useEntitlements()
  const caseAddonsEnabled = hasEntitlement("case_addons")
  const { caseDurations, caseDurationsIsLoading } = useCaseDurations({
    caseId,
    workspaceId,
    enabled: caseAddonsEnabled,
  })
  const { caseDurationDefinitions, caseDurationDefinitionsIsLoading } =
    useCaseDurationDefinitions(workspaceId, caseAddonsEnabled)

  return (
    <div className="min-w-0">
      {caseAddonsEnabled ? (
        <div className="max-w-[min(48vw,36rem)] overflow-x-auto">
          <CaseDurationMetrics
            durations={caseDurations}
            definitions={caseDurationDefinitions}
            isLoading={
              caseDurationsIsLoading || caseDurationDefinitionsIsLoading
            }
            variant="inline"
          />
        </div>
      ) : null}
    </div>
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
  return (
    <>
      <TableSelectionActionsBar />
      <TableLinkRowsToCaseCommand />
      <TableInsertButton />
    </>
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
    const workflowView =
      searchParams?.get("view") === "list" ? "list" : "folders"
    return {
      title: (
        <WorkflowsBreadcrumb
          workspaceId={workspaceId}
          path={
            workflowView === "folders"
              ? (searchParams?.get("path") ?? "/")
              : "/"
          }
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

  if (pagePath.startsWith("/runs")) {
    return {
      title: "Runs",
    }
  }

  if (pagePath.startsWith("/agents")) {
    // Tags page
    if (pagePath === "/agents/tags") {
      return {
        title: "Agents",
        actions: <AgentsActions />,
      }
    }

    // Agent preset detail page
    const agentPresetMatch = pagePath.match(/^\/agents\/([^/]+)$/)
    if (agentPresetMatch) {
      const presetId = agentPresetMatch[1]
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
      pagePath === "/cases/dropdowns" ||
      pagePath === "/cases/closure-requirements" ||
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
    return {
      title: "Integrations",
      actions: <IntegrationsActions />,
    }
  }

  if (pagePath.startsWith("/actions")) {
    return {
      title: "Actions",
      actions: <RegistryActionsControls />,
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

  if (pagePath === "/members") {
    return {
      title: "Members",
      actions: <MembersActions view={MembersViewMode.Members} />,
    }
  }

  if (pagePath === "/members/roles") {
    return {
      title: "Roles",
      actions: <MembersActions view={MembersViewMode.Roles} />,
    }
  }

  if (pagePath === "/members/groups") {
    return {
      title: "Groups",
      actions: <MembersActions view={MembersViewMode.Groups} />,
    }
  }

  if (pagePath.startsWith("/inbox")) {
    return {
      title: "Inbox",
    }
  }

  return null
}

export function ControlsHeader({ onToggleChat }: ControlsHeaderProps = {}) {
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const workspaceId = useWorkspaceId()
  const pagePath = pathname
    ? pathname.replace(`/workspaces/${workspaceId}`, "") || "/"
    : "/"
  const isCasesListPage = pagePath === "/cases"
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

  useEffect(() => {
    if (!onToggleChat) {
      return
    }
    const DOUBLE_TAP_WINDOW_MS = 1200
    let pendingAt: number | null = null

    const isEditableTarget = (target: EventTarget | null) => {
      if (!(target instanceof HTMLElement)) {
        return false
      }
      const tagName = target.tagName
      return (
        target.isContentEditable ||
        tagName === "INPUT" ||
        tagName === "TEXTAREA" ||
        tagName === "SELECT" ||
        target.getAttribute("role") === "textbox"
      )
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (
        event.repeat ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        event.shiftKey
      ) {
        pendingAt = null
        return
      }
      if (event.key.toLowerCase() !== CHAT_TOGGLE_KEY) {
        pendingAt = null
        return
      }
      if (isEditableTarget(event.target)) {
        pendingAt = null
        return
      }

      const now = Date.now()
      if (pendingAt === null || now - pendingAt > DOUBLE_TAP_WINDOW_MS) {
        pendingAt = now
        return
      }

      pendingAt = null
      event.preventDefault()
      onToggleChat()
    }

    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [onToggleChat])

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
        {isCasesListPage && <CasesSelectionActionsBar enabled />}
      </div>

      {/* Right section: actions / timestamp / chat toggle */}
      <div className="flex items-center gap-2 flex-shrink-0">
        {pageConfig.actions
          ? pageConfig.actions
          : caseId && (
              <CaseStatusControl caseId={caseId} workspaceId={workspaceId} />
            )}

        {onToggleChat && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                onClick={onToggleChat}
              >
                <PanelRight className="h-4 w-4 text-muted-foreground" />
                <span className="sr-only">Toggle Chat</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent
              side="bottom"
              align="end"
              alignOffset={-10}
              collisionPadding={16}
              className="border-0 bg-transparent p-0 shadow-none"
              sideOffset={8}
            >
              <span className="inline-flex items-center gap-1">
                <Kbd>C</Kbd>
                <span className="inline-flex h-5 items-center rounded border bg-muted px-1.5 text-[10px] font-medium text-muted-foreground">
                  then
                </span>
                <Kbd>C</Kbd>
              </span>
            </TooltipContent>
          </Tooltip>
        )}
      </div>
    </header>
  )
}
