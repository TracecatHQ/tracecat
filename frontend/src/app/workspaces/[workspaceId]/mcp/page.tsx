"use client"

import { notFound } from "next/navigation"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { WorkspaceMcpAccess } from "@/components/organization/workspace-mcp-access"

export default function WorkspaceMcpAccessPage() {
  const canReadWorkspace = useScopeCheck("workspace:read")

  if (canReadWorkspace === false) {
    notFound()
  }
  if (canReadWorkspace === undefined) {
    return null
  }

  return <WorkspaceMcpAccess />
}
