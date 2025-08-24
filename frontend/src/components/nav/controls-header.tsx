"use client"

import { useQueryClient } from "@tanstack/react-query"
import { format, formatDistanceToNow } from "date-fns"
import { Calendar, PanelRight, Plus } from "lucide-react"
import Link from "next/link"
import { usePathname, useSearchParams } from "next/navigation"
import { type ReactNode, useState } from "react"
import type { OAuthGrantType } from "@/client"
import { entitiesCreateEntity, entitiesCreateRelationGlobal } from "@/client"
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
import { CreateRelationDialog } from "@/components/entities/create-relation-dialog"
import {
  EntitiesViewMode,
  EntitiesViewToggle,
} from "@/components/entities/entities-view-toggle"
import { CreateTableDialog } from "@/components/tables/table-create-dialog"
import { TableInsertButton } from "@/components/tables/table-insert-button"
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
import { entityEvents } from "@/lib/entity-events"
import {
  useGetCase,
  useGetPrompt,
  useGetTable,
  useIntegrationProvider,
  useLocalStorage,
} from "@/lib/hooks"
import { useEntity } from "@/lib/hooks/use-entities"
import { useWorkspace } from "@/providers/workspace"

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
  return (
    <div className="flex items-center gap-2">
      <Button
        variant="outline"
        size="sm"
        className="h-7 bg-white"
        onClick={() => entityEvents.emitAddField()}
      >
        <Plus className="mr-1 h-3.5 w-3.5" />
        Add field
      </Button>
      <Button
        variant="outline"
        size="sm"
        className="h-7 bg-white"
        onClick={() => entityEvents.emitAddRelation()}
      >
        <Plus className="mr-1 h-3.5 w-3.5" />
        Add relation
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
  const [view, setView] = useLocalStorage("cases-view", CasesViewMode.Cases)
  const [dialogOpen, setDialogOpen] = useState(false)

  return (
    <>
      <CasesViewToggle view={view} onViewChange={setView} />
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
  const { workspace } = useWorkspace()

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

function CustomFieldsActions() {
  return <AddCustomField />
}

function EntitiesActions() {
  const [createEntityDialogOpen, setCreateEntityDialogOpen] = useState(false)
  const [createRelationDialogOpen, setCreateRelationDialogOpen] =
    useState(false)
  const [createRelationError, setCreateRelationError] = useState<string | null>(
    null
  )
  const [view, setView] = useLocalStorage(
    "entities-view",
    EntitiesViewMode.Fields
  )
  const [includeInactive, setIncludeInactive] = useLocalStorage(
    "entities-include-inactive",
    false
  )
  const { workspaceId } = useWorkspace()
  const searchParams = useSearchParams()
  const queryClient = useQueryClient()

  // Early return if no workspace is selected
  if (!workspaceId) {
    return null
  }

  const handleCreateEntity = async (data: {
    name: string
    display_name: string
    description?: string
    icon?: string
  }) => {
    try {
      await entitiesCreateEntity({
        workspaceId,
        requestBody: {
          name: data.name,
          display_name: data.display_name,
          description: data.description,
          icon: data.icon,
        },
      })

      queryClient.invalidateQueries({
        queryKey: ["entities", workspaceId],
      })

      toast({
        title: "Entity created",
        description: `${data.display_name} has been created successfully.`,
      })
    } catch (_error) {
      // Log error but don't use console.error in production
      // The error is already handled by the toast
      toast({
        title: "Error creating entity",
        description: "Failed to create entity. Please try again.",
        variant: "destructive",
      })
      // Don't rethrow - it's already handled
    }
  }

  const handleCreateRelationGlobal = async (data: {
    source_entity_id: string
    source_key: string
    display_name: string
    relation_type: import("@/client").RelationType
    target_entity_id: string
  }) => {
    if (!workspaceId) return
    try {
      setCreateRelationError(null)
      await entitiesCreateRelationGlobal({
        workspaceId,
        requestBody: {
          source_entity_id: data.source_entity_id,
          source_key: data.source_key,
          display_name: data.display_name,
          relation_type: data.relation_type,
          target_entity_id: data.target_entity_id,
        },
      })
      queryClient.invalidateQueries({
        queryKey: ["workspace-relations", workspaceId],
      })
      toast({ title: "Relation created", description: "Relation added." })
      setCreateRelationDialogOpen(false)
    } catch (error) {
      console.error("Failed to create relation:", error)
      let message = "Failed to create relation. Please try again."
      if (error && typeof error === "object") {
        const err = error as { body?: { detail?: string }; message?: string }
        message = err.body?.detail || err.message || message
      }
      setCreateRelationError(message)
    }
  }

  return (
    <div className="flex items-center gap-2">
      {/* Include inactive toggle on the left of the views switch */}
      <div className="flex items-center gap-2 mr-4">
        <Label
          htmlFor="entities-include-inactive-global"
          className="text-xs text-muted-foreground"
        >
          Include inactive
        </Label>
        <Switch
          id="entities-include-inactive-global"
          checked={includeInactive}
          onCheckedChange={setIncludeInactive}
        />
      </div>
      <EntitiesViewToggle view={view} onViewChange={setView} />
      {view === EntitiesViewMode.Fields ? (
        <>
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
            onSubmit={handleCreateEntity}
          />
        </>
      ) : (
        <>
          <Button
            variant="outline"
            size="sm"
            className="h-7 bg-white"
            onClick={() => setCreateRelationDialogOpen(true)}
          >
            <Plus className="mr-1 h-3.5 w-3.5" />
            Add relation
          </Button>
          <CreateRelationDialog
            open={createRelationDialogOpen}
            onOpenChange={(open) => {
              setCreateRelationDialogOpen(open)
              if (!open) setCreateRelationError(null)
            }}
            errorMessage={createRelationError || undefined}
            onSubmit={async (data) => {
              // Ensure source is provided in global mode
              if (!data.source_entity_id) {
                setCreateRelationError("Select a source entity")
                return
              }
              await handleCreateRelationGlobal({
                source_entity_id: data.source_entity_id,
                source_key: data.source_key,
                display_name: data.display_name,
                relation_type: data.relation_type,
                target_entity_id: data.target_entity_id,
              })
            }}
            // Show and require the source selector for global creation
            showSourceSelector
            sourceEntityId={searchParams?.get("source") ?? undefined}
          />
        </>
      )}
    </div>
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
  const { data: prompt } = useGetPrompt({ workspaceId, promptId: runbookId })

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
            {prompt?.title || runbookId}
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
          <BreadcrumbPage className="font-semibold">
            {entity?.display_name || entityId}
          </BreadcrumbPage>
        </BreadcrumbItem>
      </BreadcrumbList>
    </Breadcrumb>
  )
}

function getPageConfig(
  pathname: string,
  workspaceId: string,
  searchParams?: ReturnType<typeof useSearchParams>
): PageConfig | null {
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

  if (pagePath.startsWith("/members")) {
    return {
      title: "Members",
      actions: <MembersActions />,
    }
  }

  if (pagePath.startsWith("/custom-fields")) {
    return {
      title: "Custom fields",
      actions: <CustomFieldsActions />,
    }
  }

  if (pagePath.startsWith("/entities")) {
    // Check if this is an entity detail page
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

    return {
      title: "Entities",
      actions: <EntitiesActions />,
    }
  }

  if (pagePath.startsWith("/runbooks")) {
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
  const { workspaceId } = useWorkspace()

  const pageConfig =
    pathname && workspaceId
      ? getPageConfig(pathname, workspaceId, searchParams)
      : null

  if (!pageConfig) {
    return null
  }

  // Check if this is a case detail page to show timestamp
  const pagePath =
    pathname && workspaceId
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
          : isCaseDetail &&
            workspaceId && (
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
