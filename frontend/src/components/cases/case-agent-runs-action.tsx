"use client"

import { MousePointerClickIcon } from "lucide-react"
import Link from "next/link"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { Button } from "@/components/ui/button"
import { useEntitlements } from "@/hooks/use-entitlements"
import { getCaseAgentRunsHref } from "@/lib/inbox"

interface CaseAgentRunsActionProps {
  caseId: string
  workspaceId: string
}

/** Link from a case to agent runs associated with that case. */
export function CaseAgentRunsAction({
  caseId,
  workspaceId,
}: CaseAgentRunsActionProps) {
  const canReadInbox = useScopeCheck("inbox:read")
  const { hasEntitlement, isLoading } = useEntitlements()

  if (isLoading || canReadInbox !== true || !hasEntitlement("agent_addons")) {
    return null
  }

  return (
    <Button asChild variant="outline" size="sm" className="h-7">
      <Link href={getCaseAgentRunsHref(workspaceId, caseId)}>
        <MousePointerClickIcon className="mr-1.5 size-3.5" />
        View agent runs
      </Link>
    </Button>
  )
}
