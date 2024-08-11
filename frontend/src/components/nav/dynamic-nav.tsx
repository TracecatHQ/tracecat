"use client"

import React from "react"
import { type Params } from "next/dist/shared/lib/router/utils/route-matcher"
import { useParams, usePathname } from "next/navigation"

import { DashboardNav } from "@/components/nav/dashboard-nav"
import { Navbar } from "@/components/nav/navbar"
import { WorkbenchNav } from "@/components/nav/workbench-nav"

export function DynamicNavbar() {
  const pathname = usePathname()
  const params = useParams()
  return <Navbar>{getNavBar(pathname, params)}</Navbar>
}

function getNavBar(pathname: string, params: Params) {
  const { workspaceId, workflowId } = params
  if (pathname.includes("/workflows") && workspaceId && workflowId) {
    return <WorkbenchNav />
  }
  return <DashboardNav />
}
