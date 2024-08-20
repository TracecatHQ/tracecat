"use client"

import Link from "next/link"
import { ConeIcon } from "lucide-react"

import { siteConfig } from "@/config/site"
import { Button } from "@/components/ui/button"
import { CreateWorkflowButton } from "@/components/dashboard/create-workflow-button"
import { WorkflowsDashboardTable } from "@/components/dashboard/dashboard-table"

export function WorkflowsDashboard() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12 p-16 pt-32">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">Workflows</h2>
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
  )
}
