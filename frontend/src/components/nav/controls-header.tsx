"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { formatDistanceToNow } from "date-fns"
import {
  AlertTriangle,
  ChevronDown,
  ClockPlus,
  Flag,
  Flame,
  PanelRight,
  PenLine,
  Plus,
  Trash2,
  User,
  X,
} from "lucide-react"
import Link from "next/link"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { type ReactNode, useState } from "react"
import type { CaseStatus, EntityRead, OAuthGrantType } from "@/client"
import { entitiesCreateEntity } from "@/client"
import { AddCaseDuration } from "@/components/cases/add-case-duration"
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
import { CreateEntityDialog } from "@/components/entities/create-entity-dialog"
import { EntitySelectorPopover } from "@/components/entities/entity-selector-popover"
import { Spinner } from "@/components/loading/spinner"
import { CreateRecordDialog } from "@/components/records/create-record-dialog"
import { CreateTableDialog } from "@/components/tables/table-create-dialog"
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
import { Badge } from "@/components/ui/badge"
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
import { Label } from "@/components/ui/label"
import { SidebarTrigger } from "@/components/ui/sidebar"
import { Switch } from "@/components/ui/switch"
import { toast } from "@/components/ui/use-toast"
import { AddWorkspaceMember } from "@/components/workspaces/add-workspace-member"
import {
  NewCredentialsDialog,
  NewCredentialsDialogTrigger,
} from "@/components/workspaces/add-workspace-secret"
import { useEntities, useEntity } from "@/hooks/use-entities"
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import { useLocalStorage } from "@/hooks/use-local-storage"
import { useCreateRunbook } from "@/hooks/use-runbook"
import { useWorkspaceDetails, useWorkspaceMembers } from "@/hooks/use-workspace"
import { getDisplayName } from "@/lib/auth"
import { entityEvents } from "@/lib/entity-events"
import {
  useGetCase,
  useGetRunbook,
  useGetTable,
  useIntegrationProvider,
  useUpdateCase,
} from "@/lib/hooks"
import { getIconByName } from "@/lib/icons"
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

function EntitiesDetailHeaderActions() {
  const [includeInactive, setIncludeInactive] = useLocalStorage(
    "entities-include-inactive",
    false
  )
  return (
    <div className="flex items-center gap-4">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Label
          htmlFor="entities-include-inactive"
          className="text-xs text-muted-foreground"
        >
          Include inactive
        </Label>
        <Switch
          id="entities-include-inactive"
          checked={includeInactive}
          onCheckedChange={setIncludeInactive}
        />
      </div>
      <Button
        variant="outline"
        size="sm"
        className="h-7 bg-white"
        onClick={() => entityEvents.emitAddField()}
      >
        <Plus className="mr-1 h-3.5 w-3.5" />
        Add field
      </Button>
    </div>
  )
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

function TablesActions() {
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
        Create table
      </Button>
      <CreateTableDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </>
  )
}

function CasesActions() {
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const workspaceId = useWorkspaceId()
  const [dialogOpen, setDialogOpen] = useState(false)

  const view = pathname?.includes("/cases/custom-fields")
    ? CasesViewMode.CustomFields
    : pathname?.includes("/cases/durations")
      ? CasesViewMode.Durations
      : searchParams?.get("view") === CasesViewMode.Tags
        ? CasesViewMode.Tags
        : CasesViewMode.Cases

  const casesHref = workspaceId ? `/workspaces/${workspaceId}/cases` : undefined
  const tagsHref = (() => {
    if (!workspaceId || !casesHref) return undefined
    const params = new URLSearchParams(searchParams?.toString())
    params.set("view", CasesViewMode.Tags)
    const queryString = params.toString()
    return queryString ? `${casesHref}?${queryString}` : casesHref
  })()
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
    clearSelection,
    deleteSelected,
    bulkUpdateSelectedCases,
    isDeleting,
    isUpdating,
  } = useCaseSelection()
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false)
  const workspaceId = useWorkspaceId()
  const { members, membersLoading } = useWorkspaceMembers(workspaceId)

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

  const isBusy = Boolean(isDeleting) || Boolean(isUpdating)
  const canUpdate = Boolean(bulkUpdateSelectedCases) && !isBusy
  const pluralisedCases = `${selectedCount} case${selectedCount === 1 ? "" : "s"}`

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
  return (
    <NewCredentialsDialog>
      <NewCredentialsDialogTrigger asChild>
        <Button variant="outline" size="sm" className="h-7 bg-white">
          <Plus className="mr-1 h-3.5 w-3.5" />
          Add credential
        </Button>
      </NewCredentialsDialogTrigger>
    </NewCredentialsDialog>
  )
}

function EntitiesActions() {
  const [createEntityDialogOpen, setCreateEntityDialogOpen] = useState(false)
  const workspaceId = useWorkspaceId()
  const queryClient = useQueryClient()

  const { mutateAsync: createEntity, isPending: isCreatingEntity } =
    useMutation({
      mutationFn: async (data: {
        key: string
        display_name: string
        description?: string
        icon?: string
      }) =>
        await entitiesCreateEntity({
          workspaceId,
          requestBody: {
            key: data.key,
            display_name: data.display_name,
            description: data.description,
            icon: data.icon,
          },
        }),
      onSuccess: (_, data) => {
        queryClient.invalidateQueries({ queryKey: ["entities", workspaceId] })
        toast({
          title: "Entity created",
          description: `${data.display_name} has been created successfully.`,
        })
        setCreateEntityDialogOpen(false)
      },
    })

  return (
    <div className="flex items-center gap-2">
      <Button
        variant="outline"
        size="sm"
        className="h-7 bg-white"
        onClick={() => setCreateEntityDialogOpen(true)}
      >
        <Plus className="mr-1 h-3.5 w-3.5" />
        Add entity
      </Button>
      <CreateEntityDialog
        open={createEntityDialogOpen}
        onOpenChange={setCreateEntityDialogOpen}
        onSubmit={async (data) => {
          await createEntity(data)
        }}
        isSubmitting={isCreatingEntity}
      />
    </div>
  )
}

function RunbooksActions() {
  const workspaceId = useWorkspaceId()
  const router = useRouter()
  const { createRunbook, createRunbookPending } = useCreateRunbook(workspaceId)

  const handleCreateRunbook = async () => {
    try {
      // Create a runbook without chat_id - backend will auto-generate title and content
      const runbook = await createRunbook({})

      // Navigate to the new runbook
      router.push(`/workspaces/${workspaceId}/runbooks/${runbook.id}`)
    } catch (error) {
      toast({
        title: "Failed to create runbook",
        description:
          error instanceof Error
            ? error.message
            : "An unexpected error occurred. Please try again.",
        variant: "destructive",
      })
    }
  }

  return (
    <Button
      variant="outline"
      size="sm"
      className="h-7 bg-white"
      onClick={handleCreateRunbook}
      disabled={createRunbookPending}
      title="Create runbooks"
    >
      <Plus className="mr-1 h-3.5 w-3.5" />
      {createRunbookPending ? "Creating..." : "Add runbook"}
    </Button>
  )
}

function RecordsActions() {
  const workspaceId = useWorkspaceId()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [selectedEntityId, setSelectedEntityId] = useState<string>("")
  const { entities } = useEntities(workspaceId)

  const handleEntitySelect = (entity: EntityRead) => {
    setSelectedEntityId(entity.id)
    setDialogOpen(true)
  }

  return (
    <>
      <EntitySelectorPopover
        entities={entities}
        onSelect={handleEntitySelect}
        buttonText="Add record"
      />
      {selectedEntityId && (
        <CreateRecordDialog
          open={dialogOpen}
          onOpenChange={(open) => {
            setDialogOpen(open)
            if (!open) {
              setSelectedEntityId("")
            }
          }}
          workspaceId={workspaceId}
          entityId={selectedEntityId}
          onSuccess={() => {
            setSelectedEntityId("")
          }}
        />
      )}
    </>
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

function RunbookBreadcrumb({
  runbookId,
  workspaceId,
}: {
  runbookId: string
  workspaceId: string
}) {
  const { data: runbook } = useGetRunbook({ workspaceId, runbookId })

  return (
    <Breadcrumb>
      <BreadcrumbList className="relative z-10 flex items-center gap-2 text-sm flex-nowrap overflow-hidden whitespace-nowrap min-w-0 bg-transparent pr-1">
        <BreadcrumbItem>
          <BreadcrumbLink asChild className="font-semibold hover:no-underline">
            <Link href={`/workspaces/${workspaceId}/runbooks`}>Runbooks</Link>
          </BreadcrumbLink>
        </BreadcrumbItem>
        <BreadcrumbSeparator className="shrink-0">
          <span className="text-muted-foreground">/</span>
        </BreadcrumbSeparator>
        <BreadcrumbItem>
          <BreadcrumbPage className="font-semibold">
            {runbook?.title || runbookId}
          </BreadcrumbPage>
        </BreadcrumbItem>
      </BreadcrumbList>
    </Breadcrumb>
  )
}

function EntityBreadcrumb({
  entityId,
  workspaceId,
}: {
  entityId: string
  workspaceId: string
}) {
  const { entity } = useEntity(workspaceId, entityId)

  return (
    <Breadcrumb>
      <BreadcrumbList className="relative z-10 flex items-center gap-2 text-sm flex-nowrap overflow-hidden whitespace-nowrap min-w-0 bg-transparent pr-1">
        <BreadcrumbItem>
          <BreadcrumbLink asChild className="font-semibold hover:no-underline">
            <Link href={`/workspaces/${workspaceId}/entities`}>Entities</Link>
          </BreadcrumbLink>
        </BreadcrumbItem>
        <BreadcrumbSeparator className="shrink-0">
          <span className="text-muted-foreground">/</span>
        </BreadcrumbSeparator>
        <BreadcrumbItem>
          <BreadcrumbPage className="font-semibold flex items-center gap-2">
            <span className="flex items-center gap-2">
              {entity?.icon &&
                (() => {
                  const IconComponent = getIconByName(entity.icon)
                  return IconComponent ? (
                    <IconComponent className="h-4 w-4 text-muted-foreground" />
                  ) : null
                })()}
              {entity?.display_name || entityId}
            </span>
            {entity?.key && (
              <Badge variant="secondary" className="text-xs font-normal">
                {entity.key}
              </Badge>
            )}
          </BreadcrumbPage>
        </BreadcrumbItem>
      </BreadcrumbList>
    </Breadcrumb>
  )
}

function getPageConfig(
  pathname: string,
  workspaceId: string,
  searchParams: ReturnType<typeof useSearchParams> | null,
  options: { runbooksEnabled: boolean }
): PageConfig | null {
  const { runbooksEnabled } = options
  const basePath = `/workspaces/${workspaceId}`

  // Remove base path to get the page route
  const pagePath = pathname.replace(basePath, "") || "/"

  // Match routes and return appropriate config
  if (pagePath === "/" || pagePath.startsWith("/workflows")) {
    return {
      title: "Workflows",
      actions: <WorkflowsActions />,
    }
  }

  if (pagePath.startsWith("/cases")) {
    if (
      pagePath === "/cases/custom-fields" ||
      pagePath === "/cases/durations"
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
    }
  }

  if (pagePath.startsWith("/credentials")) {
    return {
      title: "Credentials",
      actions: <CredentialsActions />,
    }
  }

  if (pagePath.startsWith("/entities")) {
    // Entity detail page
    const entityMatch = pagePath.match(/^\/entities\/([^/]+)$/)
    if (entityMatch) {
      const entityId = entityMatch[1]
      return {
        title: (
          <EntityBreadcrumb entityId={entityId} workspaceId={workspaceId} />
        ),
        actions: <EntitiesDetailHeaderActions />,
      }
    }
    // Index
    return {
      title: "Entities",
      actions: <EntitiesActions />,
    }
  }

  if (pagePath.startsWith("/members")) {
    return {
      title: "Members",
      actions: <MembersActions />,
    }
  }

  if (pagePath.startsWith("/runbooks")) {
    if (!runbooksEnabled) {
      return null
    }
    // Check if this is a runbook detail page
    const runbookMatch = pagePath.match(/^\/runbooks\/([^/]+)$/)
    if (runbookMatch) {
      const runbookId = runbookMatch[1]
      return {
        title: (
          <RunbookBreadcrumb runbookId={runbookId} workspaceId={workspaceId} />
        ),
        // No actions for runbook detail pages
      }
    }

    return {
      title: "Runbooks",
      actions: <RunbooksActions />,
    }
  }

  if (pagePath.startsWith("/records")) {
    return {
      title: "Records",
      actions: <RecordsActions />,
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
  const { isFeatureEnabled } = useFeatureFlag()
  const runbooksEnabled = isFeatureEnabled("runbooks")
  const pagePath = pathname
    ? pathname.replace(`/workspaces/${workspaceId}`, "") || "/"
    : "/"
  const isCaseDetail = pagePath.match(/^\/cases\/([^/]+)$/)
  const caseId = isCaseDetail ? isCaseDetail[1] : null

  const pageConfig = pathname
    ? getPageConfig(pathname, workspaceId, searchParams ?? null, {
        runbooksEnabled,
      })
    : null
  const { caseData } = useGetCase(
    { caseId: caseId ?? "", workspaceId },
    { enabled: Boolean(caseId) }
  )

  if (!pageConfig) {
    return null
  }

  // Check if this is a case detail page to show timestamp
  const headerBackgroundClass = caseId
    ? caseData?.status
      ? CASE_STATUS_TINTS[caseData.status]
      : "bg-muted/5 dark:bg-muted/[0.12]"
    : "bg-background"

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
