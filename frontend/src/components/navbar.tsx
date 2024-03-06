"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { usePathname, useSearchParams } from "next/navigation"
import axios from "axios"
import { BellRingIcon, WorkflowIcon } from "lucide-react"

import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { UserNav } from "@/components/user-nav"
import WorkflowSwitcher from "@/components/workflow-switcher"

export function Navbar() {
  const [enableWorkflow, setEnableWorkflow] = useState(false)
  const searchParams = useSearchParams()
  const selectedWorkflowId = searchParams.get("id")
  const pathname = usePathname()

  useEffect(() => {
    const updateWorkflowStatus = async () => {
      if (selectedWorkflowId) {
        const status = enableWorkflow ? "online" : "offline"
        try {
          await axios.post(
            `http://localhost:8000/workflows/${selectedWorkflowId}`,
            JSON.stringify({
              status: status,
            }),
            {
              headers: {
                "Content-Type": "application/json",
              },
            }
          )
          console.log(`Workflow ${selectedWorkflowId} set to ${status}`)
        } catch (error) {
          console.error("Failed to update workflow status:", error)
        }
      }
    }

    updateWorkflowStatus()
  }, [enableWorkflow, selectedWorkflowId])

  return (
    <div className="border-b">
      <div className="flex h-16 items-center px-4">
        <div className="flex space-x-8">
          {/* TODO: Ensure that workflow switcher doesn't make an API call to update
              workflows when page is switched between workflow view and cases view
          */}
          <WorkflowSwitcher />
          <Tabs value={pathname === "/" ? "workflow" : "cases"}>
            <TabsList className="grid w-full grid-cols-2">
              <Link href="/" className="w-full" passHref>
                <TabsTrigger className="w-full" value="workflow">
                  <WorkflowIcon className="mr-2 h-4 w-4" />
                  Workflow
                </TabsTrigger>
              </Link>
              <Link href="/cases" className="w-full" passHref>
                <TabsTrigger className="w-full" value="cases">
                  <BellRingIcon className="mr-2 h-4 w-4" />
                  Cases
                </TabsTrigger>
              </Link>
            </TabsList>
          </Tabs>
        </div>
        <div className="ml-auto flex items-center space-x-8">
          <div className="flex items-center space-x-2">
            <Switch
              id="enable-workflow"
              checked={enableWorkflow}
              onCheckedChange={(newCheckedState) =>
                setEnableWorkflow(newCheckedState)
              }
            />
            <Label className="w-32" htmlFor="enable-workflow">
              {enableWorkflow ? "Disable workflow" : "Enable workflow"}
            </Label>
          </div>
          <UserNav />
        </div>
      </div>
    </div>
  )
}
