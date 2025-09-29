"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { format, formatDistanceToNow } from "date-fns"
import { Calendar, PanelRight, Plus } from "lucide-react"
import Link from "next/link"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { type ReactNode, useState } from "react"
import type { EntityRead, OAuthGrantType } from "@/client"
import { entitiesCreateEntity } from "@/client"
import { AddCustomField } from "@/components/cases/add-custom-field"
import { CreateCaseDialog } from "@/components/cases/case-create-dialog"
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
import { CreateRecordDialog } from "@/components/records/create-record-dialog"
import { CreateTableDialog } from "@/components/tables/table-create-dialog"
import { TableInsertButton } from "@/components/tables/table-insert-button"
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
import { useWorkspaceDetails } from "@/hooks/use-workspace"
import { entityEvents } from "@/lib/entity-events"
import {
  useGetCase,
  useGetRunbook,
  useGetTable,
  useIntegrationProvider,
} from "@/lib/hooks"
import { getIconByName } from "@/lib/icons"
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
  const workspaceId = useWorkspaceId()
  const [dialogOpen, setDialogOpen] = useState(false)

  const view = pathname?.includes("/cases/custom-fields")
    ? CasesViewMode.CustomFields
    : CasesViewMode.Cases

  const casesHref = workspaceId ? `/workspaces/${workspaceId}/cases` : undefined
  const customFieldsHref = workspaceId
    ? `/workspaces/${workspaceId}/cases/custom-fields`
    : undefined

  return (
    <>
      <CasesViewToggle
        view={view}
        casesHref={casesHref}
        customFieldsHref={customFieldsHref}
      />
      {view === CasesViewMode.CustomFields ? (
        <AddCustomField />
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
      <BreadcrumbList className="relative z-10 flex items-center gap-2 text-sm flex-nowrap overflow-hidden whitespace-nowrap min-w-0 bg-white pr-1">
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
}: {
  caseId: string
  workspaceId: string
}) {
  const { caseData } = useGetCase({ caseId, workspaceId })

  if (!caseData) {
    return null
  }

  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground min-w-0">
      <span className="hidden sm:flex items-center gap-1 min-w-0">
        <Calendar className="h-3 w-3 flex-shrink-0" />
        <span className="hidden lg:inline flex-shrink-0">Created</span>
        <span className="truncate min-w-0">
          {format(new Date(caseData.created_at), "MMM d, yyyy, h:mm a")}
        </span>
      </span>
      <span className="hidden sm:inline flex-shrink-0">•</span>
      <span className="flex items-center gap-1 min-w-0">
        <span className="hidden sm:inline flex-shrink-0">Updated</span>
        <span className="truncate min-w-0">
          {formatDistanceToNow(new Date(caseData.updated_at), {
            addSuffix: true,
          })}
        </span>
      </span>
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
      <BreadcrumbList className="relative z-10 flex items-center gap-2 text-sm flex-nowrap overflow-hidden whitespace-nowrap min-w-0 bg-white pr-1">
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
      <BreadcrumbList className="relative z-10 flex items-center gap-2 text-sm flex-nowrap overflow-hidden whitespace-nowrap min-w-0 bg-white pr-1">
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
      <BreadcrumbList className="relative z-10 flex items-center gap-2 text-sm flex-nowrap overflow-hidden whitespace-nowrap min-w-0 bg-white pr-1">
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
      <BreadcrumbList className="relative z-10 flex items-center gap-2 text-sm flex-nowrap overflow-hidden whitespace-nowrap min-w-0 bg-white pr-1">
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
    if (pagePath === "/cases/custom-fields") {
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

  const pageConfig = pathname
    ? getPageConfig(pathname, workspaceId, searchParams ?? null, {
        runbooksEnabled,
      })
    : null

  if (!pageConfig) {
    return null
  }

  // Check if this is a case detail page to show timestamp
  const pagePath = pathname
    ? pathname.replace(`/workspaces/${workspaceId}`, "") || "/"
    : "/"
  const isCaseDetail = pagePath.match(/^\/cases\/([^/]+)$/)

  return (
    <header className="flex h-10 items-center border-b px-3 overflow-hidden">
      {/* Left section: sidebar toggle + title */}
      <div className="flex items-center gap-3 min-w-0">
        <SidebarTrigger className="h-7 w-7 flex-shrink-0" />
        {typeof pageConfig.title === "string" ? (
          <h1 className="text-sm font-semibold">{pageConfig.title}</h1>
        ) : (
          pageConfig.title
        )}
      </div>

      {/* Middle spacer keeps actions/right buttons from overlapping title */}
      <div className="flex-1 min-w-[1rem]" />

      {/* Right section: actions / timestamp / chat toggle */}
      <div className="flex items-center gap-2 flex-shrink-0">
        {pageConfig.actions
          ? pageConfig.actions
          : isCaseDetail && (
              <CaseTimestamp
                caseId={isCaseDetail[1]}
                workspaceId={workspaceId}
              />
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
