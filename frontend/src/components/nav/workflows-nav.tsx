"use client"

import React, { useEffect, useState } from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { useWorkflowMetadata } from "@/providers/workflow"
import { Session } from "@supabase/supabase-js"
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
import WorkflowSwitcher from "@/components/nav/workflow-switcher"
import { QueryBuilder } from "@/components/query-builder"

interface NavbarProps extends React.HTMLAttributes<HTMLDivElement> {
  session: Session | null
}

export default function WorkflowsNavbar({ session }: NavbarProps) {
  const { workflowId, isLoading, isOnline, setIsOnline } = useWorkflowMetadata()

  if (isLoading) {
    return null
  }
  return (
    workflowId && (
      <div className="flex w-full items-center space-x-8">
        <WorkflowSwitcher session={session} />
        <TabSwitcher workflowId={workflowId} />
        <SearchBar workflowId={workflowId} />
        <div className="flex flex-1 items-center justify-end space-x-2">
          <Switch
            id="enable-workflow"
            checked={isOnline}
            onCheckedChange={setIsOnline}
          />
          <Label
            className="w-30 text-xs text-muted-foreground"
            htmlFor="enable-workflow"
          >
            {isOnline ? "Pause" : "Publish"}
          </Label>
        </div>
      </div>
    )
  )
}

function TabSwitcher({ workflowId }: { workflowId: string }) {
  const pathname = usePathname()
  return (
    <Tabs value={pathname.endsWith("/cases") ? "cases" : "workflow"}>
      <TabsList className="grid w-full grid-cols-2">
        <TabsTrigger className="w-full py-0" value="workflow" asChild>
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
  )
}

function SearchBar({ workflowId }: { workflowId: string }) {
  const [openEventSearch, setOpenEventSearch] = useState(false)
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
  return (
    <Drawer open={openEventSearch} onOpenChange={setOpenEventSearch}>
      <DrawerTrigger asChild>
        <Button
          variant="outline"
          className={cn(
            "md:w-30 relative h-8 w-full justify-start rounded-[0.5rem] bg-background text-xs font-normal text-muted-foreground shadow-none sm:pr-12 lg:w-48"
          )}
        >
          <span className="hidden lg:inline-flex">Search events...</span>
          <span className="inline-flex lg:hidden">Search...</span>
          <kbd className="pointer-events-none absolute right-[0.3rem] top-[0.3rem] hidden h-5 select-none items-center gap-1 rounded border bg-muted px-1.5 font-mono text-[10px] font-medium opacity-100 sm:flex">
            <span>⌘</span>E
          </kbd>
        </Button>
      </DrawerTrigger>
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
  )
}
