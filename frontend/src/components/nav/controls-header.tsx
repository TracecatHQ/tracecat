"use client"

import { PanelLeftClose, PanelLeftOpen, Plus } from "lucide-react"
import { usePathname, useSearchParams } from "next/navigation"
import { type ReactNode, useState } from "react"
import { CreateCaseDialog } from "@/components/cases/case-create-dialog"
import { CreateWorkflowButton } from "@/components/dashboard/create-workflow-button"
import {
  FolderViewToggle,
  ViewMode,
} from "@/components/dashboard/folder-view-toggle"
import { CreateTableDialog } from "@/components/tables/table-create-dialog"
import { Button } from "@/components/ui/button"
import { SidebarTrigger, useSidebar } from "@/components/ui/sidebar"
import { AddWorkspaceMember } from "@/components/workspaces/add-workspace-member"
import {
  NewCredentialsDialog,
  NewCredentialsDialogTrigger,
} from "@/components/workspaces/add-workspace-secret"
import { useLocalStorage } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { useWorkspace } from "@/providers/workspace"

interface PageConfig {
  title: string
  actions?: ReactNode
}

function WorkflowsActions() {
  const searchParams = useSearchParams()
  const currentPath = searchParams.get("path") || null
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
        Create case
      </Button>
      <CreateCaseDialog open={dialogOpen} onOpenChange={setDialogOpen} />
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

function getPageConfig(
  pathname: string,
  workspaceId: string
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
    return {
      title: "Cases",
      actions: <CasesActions />,
    }
  }

  if (pagePath.startsWith("/tables")) {
    return {
      title: "Tables",
      actions: <TablesActions />,
    }
  }

  if (pagePath.startsWith("/integrations")) {
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
    }
  }

  return null
}

export function ControlsHeader() {
  const { state } = useSidebar()
  const pathname = usePathname()
  const { workspaceId } = useWorkspace()

  const pageConfig = getPageConfig(pathname, workspaceId)

  if (!pageConfig) {
    return null
  }

  return (
    <header className="flex h-14 items-center justify-between border-b px-6">
      <div className="flex items-center gap-3">
        <SidebarTrigger className="h-7 w-7">
          {state === "collapsed" ? (
            <PanelLeftOpen className="h-4 w-4" />
          ) : (
            <PanelLeftClose className="h-4 w-4" />
          )}
        </SidebarTrigger>
        <h1 className="text-sm font-semibold">{pageConfig.title}</h1>
      </div>

      {pageConfig.actions && (
        <div className="flex items-center gap-2">{pageConfig.actions}</div>
      )}
    </header>
  )
}
