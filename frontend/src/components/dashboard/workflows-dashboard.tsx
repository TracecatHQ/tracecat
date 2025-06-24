"use client"

import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import { CreateWorkflowButton } from "@/components/dashboard/create-workflow-button"
import { WorkflowsDashboardTable } from "@/components/dashboard/dashboard-table"
import {
  FolderViewToggle,
  ViewMode,
} from "@/components/dashboard/folder-view-toggle"
import { WorkflowFoldersTable } from "@/components/dashboard/workflow-folders-table"
import { WorkflowTagsSidebar } from "@/components/dashboard/workflow-tags-sidebar"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { useLocalStorage, useTags } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { useWorkspace } from "@/providers/workspace"

export function WorkflowsDashboard() {
  const router = useRouter()
  const { workspaceId } = useWorkspace()
  const { tags } = useTags(workspaceId)
  const searchParams = useSearchParams()
  const queryTag = searchParams.get("tag")

  const [view, setView] = useLocalStorage("folder-view", ViewMode.Tags)

  // If we navigate to a tag that doesn't exist, redirect to the workflows page
  if (queryTag && !tags?.some((tag) => tag.name === queryTag)) {
    router.push(`/workspaces/${workspaceId}/workflows`)
    return null
  }

  if (view === ViewMode.Folders) {
    return (
      <div className="size-full overflow-auto">
        <div className="container h-full gap-8 py-16">
          <WorkflowFoldersTable view={view} setView={setView} />
        </div>
      </div>
    )
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container grid h-full grid-cols-6 gap-8 py-16">
        <div className="col-span-1">
          <WorkflowTagsSidebar workspaceId={workspaceId} />
        </div>
        <div className="col-span-5 flex flex-col space-y-12">
          <div className="flex w-full">
            <div className="items-start space-y-3 text-left">
              <Breadcrumb>
                <BreadcrumbList>
                  <BreadcrumbItem>
                    <BreadcrumbLink
                      asChild
                      className={cn(
                        "flex items-center",
                        queryTag ? "text-muted-foreground" : "text-foreground"
                      )}
                    >
                      <Link href={`/workspaces/${workspaceId}/workflows`}>
                        <h2 className="text-2xl font-semibold tracking-tight">
                          Workflows
                        </h2>
                      </Link>
                    </BreadcrumbLink>
                  </BreadcrumbItem>
                  {queryTag && (
                    <>
                      <BreadcrumbSeparator>{"/"}</BreadcrumbSeparator>
                      <BreadcrumbItem>
                        <BreadcrumbLink>
                          <h2 className="text-2xl font-semibold tracking-tight text-foreground/80">
                            {queryTag}
                          </h2>
                        </BreadcrumbLink>
                      </BreadcrumbItem>
                    </>
                  )}
                </BreadcrumbList>
              </Breadcrumb>
              <p className="text-md text-muted-foreground">
                Welcome back! Here are your workflows.
              </p>
            </div>
            <div className="ml-auto flex items-center space-x-4">
              <FolderViewToggle
                defaultView={view}
                onViewChange={(view) => setView(view)}
              />
              <CreateWorkflowButton view="default" currentFolderPath={null} />
            </div>
          </div>
          <WorkflowsDashboardTable />
        </div>
      </div>
    </div>
  )
}
