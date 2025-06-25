"use client"

import {
  ShieldAlertIcon,
  Table2Icon,
  WorkflowIcon,
  ZapIcon,
} from "lucide-react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import {
  OrganizationNavButton,
  RegistryNavButton,
} from "@/components/nav/nav-buttons"
import { WorkspaceSelector } from "@/components/workspaces/workspace-selector"
import { cn } from "@/lib/utils"
import { useWorkspace } from "@/providers/workspace"

export function WorkspaceNav() {
  const { workspaceId } = useWorkspace()
  const pathname = usePathname()
  const basePath = `/workspaces/${workspaceId}`
  const workflowsPath = `${basePath}/workflows`
  const tablesPath = `${basePath}/tables`
  const casesPath = `${basePath}/cases`
  const integrationsPath = `${basePath}/integrations`
  return (
    <nav className="flex space-x-4 lg:space-x-6">
      <div className="md:min-w-[150px] md:max-w-[200px] lg:min-w-[250px] lg:max-w-[300px]">
        <WorkspaceSelector />
      </div>
      <Link
        href={workflowsPath}
        className={cn(
          "flex-cols flex items-center text-sm font-medium text-muted-foreground transition-colors hover:text-primary",
          pathname === workflowsPath && "text-primary"
        )}
      >
        <WorkflowIcon className="mr-2 size-4" />
        <span>Workflows</span>
      </Link>
      <Link
        href={tablesPath}
        className={cn(
          "flex-cols flex items-center text-sm font-medium text-muted-foreground transition-colors hover:text-primary",
          pathname === tablesPath && "text-primary"
        )}
      >
        <Table2Icon className="mr-2 size-4" />
        <span>Tables</span>
      </Link>
      <Link
        href={casesPath}
        className={cn(
          "flex-cols flex items-center text-sm font-medium text-muted-foreground transition-colors hover:text-primary",
          pathname === casesPath && "text-primary"
        )}
      >
        <ShieldAlertIcon className="mr-2 size-4" />
        <span>Cases</span>
      </Link>
      <Link
        href={integrationsPath}
        className={cn(
          "flex-cols flex items-center text-sm font-medium text-muted-foreground transition-colors hover:text-primary",
          pathname === integrationsPath && "text-primary"
        )}
      >
        <ZapIcon className="mr-2 size-4" />
        <span>Integrations</span>
      </Link>
      <RegistryNavButton />
      <OrganizationNavButton />
    </nav>
  )
}
