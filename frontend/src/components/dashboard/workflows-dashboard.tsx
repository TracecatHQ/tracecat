"use client"

import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import { useWorkspace } from "@/providers/workspace"
import { ConeIcon } from "lucide-react"

import { siteConfig } from "@/config/site"
import { useTags } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import { CreateWorkflowButton } from "@/components/dashboard/create-workflow-button"
import { WorkflowsDashboardTable } from "@/components/dashboard/dashboard-table"
import { WorkflowTagsSidebar } from "@/components/dashboard/workflow-tags-sidebar"

export function WorkflowsDashboard() {
  const router = useRouter()
  const { workspaceId } = useWorkspace()
  const { tags } = useTags(workspaceId)
  const searchParams = useSearchParams()
  const queryTag = searchParams.get("tag")

  // If we nagivate to a tag that doesn't exist, redirect to the workflows page
  if (queryTag && !tags?.some((tag) => tag.name === queryTag)) {
    return router.push(`/workspaces/${workspaceId}/workflows`)
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
            <div className="ml-auto flex items-center space-x-2">
              <Link href={siteConfig.links.playbooks} target="_blank">
                <Button variant="outline" role="combobox" className="space-x-2">
                  <ConeIcon className="size-4 text-emerald-600" />
                  <span>Find playbook</span>
                </Button>
              </Link>
              <CreateWorkflowButton />
            </div>
          </div>
          <WorkflowsDashboardTable />
        </div>
      </div>
    </div>
  )
}
