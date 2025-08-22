"use client"

import { format, formatDistanceToNow } from "date-fns"
import { Calendar, PanelRight, Plus } from "lucide-react"
import Link from "next/link"
import { usePathname, useSearchParams } from "next/navigation"
import { type ReactNode, useState } from "react"
import type { OAuthGrantType } from "@/client"
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
import { SidebarTrigger } from "@/components/ui/sidebar"
import { AddWorkspaceMember } from "@/components/workspaces/add-workspace-member"
import {
  NewCredentialsDialog,
  NewCredentialsDialogTrigger,
} from "@/components/workspaces/add-workspace-secret"
import { useWorkspaceDetails } from "@/hooks/use-workspace"
import {
  useGetCase,
  useGetPrompt,
  useGetTable,
  useIntegrationProvider,
  useLocalStorage,
} from "@/lib/hooks"
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
  const workspaceId = useWorkspaceId()

  const pageConfig = pathname
    ? getPageConfig(pathname, workspaceId, searchParams)
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
