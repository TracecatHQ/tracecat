"use client"

import React from "react"
import { useParams, usePathname } from "next/navigation"

import { Navbar } from "@/components/nav/navbar"
import { WorkbenchNav } from "@/components/nav/workbench-nav"
import { WorkspaceNav } from "@/components/nav/workspace-nav"

type DynamicNavbarParams = {
  workspaceId?: string
  workflowId?: string
}

export function DynamicNavbar() {
  const pathname = usePathname()
  const params = useParams<DynamicNavbarParams>()
  return <Navbar>{getNavBar(pathname, params)}</Navbar>
}

function getNavBar(pathname: string, params: DynamicNavbarParams) {
  const { workspaceId, workflowId } = params
  if (pathname.includes("/workflows") && workspaceId && workflowId) {
    console.log("Rendering workflow nav")
    return <WorkbenchNav />
  } else if (pathname.includes("/workspaces") && workspaceId) {
    return <WorkspaceNav />
  } else {
    return null
  }
}
