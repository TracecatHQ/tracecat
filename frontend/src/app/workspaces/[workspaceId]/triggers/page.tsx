"use client"

import { notFound } from "next/navigation"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { WorkspaceTriggers } from "@/components/triggers/workspace-triggers"

export default function WorkspaceTriggersPage() {
  const canReadTriggers = useScopeCheck("trigger:read")

  if (canReadTriggers === false) {
    notFound()
  }
  if (canReadTriggers === undefined) {
    return null
  }

  return <WorkspaceTriggers />
}
