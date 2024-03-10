"use client"

import React, { useEffect, useState } from "react"
import Link from "next/link"
import { useParams, usePathname } from "next/navigation"
import axios from "axios"
import { BellRingIcon, WorkflowIcon } from "lucide-react"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerHeader,
  DrawerTitle,
  DrawerTrigger,
} from "@/components/ui/drawer"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Icons } from "@/components/icons"
import { QueryBuilder } from "@/components/query-builder"
import { UserNav } from "@/components/user-nav"
import WorkflowSwitcher from "@/components/workflow-switcher"

type MaybeString = string | undefined

interface NavbarProps extends React.HTMLAttributes<HTMLDivElement> {}

export function Navbar(props: NavbarProps) {
  const [openEventSearch, setOpenEventSearch] = useState(false)
  const params = useParams()
  const [workflowId, setWorkflowId] = useState<MaybeString>(
    params["id"] as MaybeString
  )
  const pathname = usePathname()
  const [enableWorkflow, setEnableWorkflow] = useState(false)

  useEffect(() => {
    const updateWorkflowStatus = async () => {
      if (workflowId) {
        const status = enableWorkflow ? "online" : "offline"
        try {
          await axios.post(
            `http://localhost:8000/workflows/${workflowId}`,
            JSON.stringify({
              status: status,
            }),
            {
              headers: {
                "Content-Type": "application/json",
              },
            }
          )
          console.log(`Workflow ${workflowId} set to ${status}`)
        } catch (error) {
          console.error("Failed to update workflow status:", error)
        }
      }
    }

    updateWorkflowStatus()
  }, [enableWorkflow])

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === "e" && (e.metaKey || e.ctrlKey)) {
        if (
          (e.target instanceof HTMLElement && e.target.isContentEditable) ||
          e.target instanceof HTMLInputElement ||
          e.target instanceof HTMLTextAreaElement ||
          e.target instanceof HTMLSelectElement
        ) {
          return
        }

        e.preventDefault()
        setOpenEventSearch((openEventSearch) => !openEventSearch)
      }
    }

    document.addEventListener("keydown", down)
    return () => document.removeEventListener("keydown", down)
  }, [])
  useEffect(() => {
    setWorkflowId(params["id"] as MaybeString)
  }, [params])

  return (
    <div className="border-b" {...props}>
      <Drawer open={openEventSearch} onOpenChange={setOpenEventSearch}>
        <div className="flex h-12 items-center px-4">
          <div className="flex items-center space-x-8">
            <Link href="/workflows">
              <Icons.logo className="ml-4 h-5 w-5" />
            </Link>
            {workflowId && (
              <>
                {/* TODO: Ensure that workflow switcher doesn't make an API call to update
            workflows when page is switched between workflow view and cases view
          */}
                <WorkflowSwitcher defaultValue={workflowId} />
                <Tabs
                  value={pathname.endsWith("/cases") ? "cases" : "workflow"}
                >
                  <TabsList className="grid w-full grid-cols-2">
                    <TabsTrigger
                      className="w-full py-0"
                      value="workflow"
                      asChild
                    >
                      <Link
                        href={`/workflows/${workflowId}`}
                        className="h-full w-full"
                        passHref
                      >
                        <WorkflowIcon className="mr-2 h-4 w-4" />
                        <span>Workflow</span>
                        <kbd className="ml-4 flex items-center justify-center gap-1 rounded border bg-muted px-1 font-mono text-[10px] font-medium text-muted-foreground opacity-100">
                          <span>Alt</span>F
                        </kbd>
                      </Link>
                    </TabsTrigger>
                    <TabsTrigger className="w-full py-0" value="cases" asChild>
                      <Link
                        href={`/workflows/${workflowId}/cases`}
                        className="h-full w-full"
                        passHref
                      >
                        <BellRingIcon className="mr-2 h-4 w-4" />
                        <span>Cases</span>
                        <kbd className="ml-4 flex items-center justify-center gap-1 rounded border bg-muted px-1 font-mono text-[10px] font-medium text-muted-foreground opacity-100">
                          <span>Alt</span>C
                        </kbd>
                      </Link>
                    </TabsTrigger>
                  </TabsList>
                </Tabs>
              </>
            )}
          </div>
          <div className="ml-auto flex items-center space-x-6">
            {workflowId && (
              <div className="flex items-center space-x-2">
                <DrawerTrigger asChild>
                  <Button
                    variant="outline"
                    className={cn(
                      "md:w-30 relative h-8 w-full justify-start rounded-[0.5rem] bg-background text-xs font-normal text-muted-foreground shadow-none sm:pr-12 lg:w-48"
                    )}
                  >
                    <span className="hidden lg:inline-flex">
                      Search events...
                    </span>
                    <span className="inline-flex lg:hidden">Search...</span>
                    <kbd className="pointer-events-none absolute right-[0.3rem] top-[0.3rem] hidden h-5 select-none items-center gap-1 rounded border bg-muted px-1.5 font-mono text-[10px] font-medium opacity-100 sm:flex">
                      <span>⌘</span>E
                    </kbd>
                  </Button>
                </DrawerTrigger>
                <Switch
                  id="enable-workflow"
                  checked={enableWorkflow}
                  onCheckedChange={(newCheckedState) =>
                    setEnableWorkflow(newCheckedState)
                  }
                />
                <Label
                  className="w-30 text-xs text-muted-foreground"
                  htmlFor="enable-workflow"
                >
                  {enableWorkflow ? "Pause" : "Publish"}
                </Label>
              </div>
            )}
            <UserNav />
          </div>
        </div>
        <DrawerContent>
          <div className="w-full space-y-4 px-4 pb-8">
            <DrawerHeader>
              <DrawerTitle>Events</DrawerTitle>
              <DrawerDescription>
                Search logs across all workflow and action runs.
              </DrawerDescription>
            </DrawerHeader>
            <QueryBuilder />
          </div>
        </DrawerContent>
      </Drawer>
    </div>
  )
}
