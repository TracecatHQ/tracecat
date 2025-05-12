"use client"

import React from "react"
import { useParams, usePathname } from "next/navigation"

import { BuilderNav } from "@/components/nav/builder-nav"
import { Navbar } from "@/components/nav/navbar"
import { OrganizationNav } from "@/components/nav/organization-nav"
import { RegistryNav } from "@/components/nav/registry-nav"
import { WorkspaceNav } from "@/components/nav/workspace-nav"

type DynamicNavbarParams = {
  workspaceId?: string
  workflowId?: string
}

export function DynamicNavbar() {
  const pathname = usePathname()
  const params = useParams<DynamicNavbarParams>()
  if (!pathname) {
    return null
  }
  return <Navbar>{getNavBar(pathname, params)}</Navbar>
}

function getNavBar(pathname: string, params: DynamicNavbarParams) {
  const { workspaceId, workflowId } = params
  if (pathname.includes("/workflows") && workspaceId && workflowId) {
    return <BuilderNav />
  } else if (pathname.includes("/workspaces") && workspaceId) {
    return <WorkspaceNav />
  } else if (pathname.includes("/registry")) {
    return <RegistryNav />
  } else if (pathname.includes("/organization")) {
    return <OrganizationNav />
  } else {
    console.log("Hit NULL", pathname)
    return null
  }
}
