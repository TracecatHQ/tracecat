"use client"

import { notFound } from "next/navigation"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { WorkspaceTunnels } from "@/components/tunnels/workspace-tunnels"

export default function WorkspaceTunnelsPage() {
  const canReadTunnels = useScopeCheck("tunnel:read")

  if (canReadTunnels === false) {
    notFound()
  }
  if (canReadTunnels === undefined) {
    return null
  }

  return <WorkspaceTunnels />
}
