"use client"

import { useParams } from "next/navigation"
import { useEffect } from "react"
import { CasePanelView } from "@/components/cases/case-panel-view"
import { useGetCase } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export default function CaseDetailPage() {
  const params = useParams<{ caseId: string }>()
  const caseId = params?.caseId
  const workspaceId = useWorkspaceId()

  const { caseData } = useGetCase({
    caseId: caseId || "",
    workspaceId,
  })

  useEffect(() => {
    if (caseData?.short_id && caseData?.summary) {
      document.title = `${caseData.short_id} | ${caseData.summary}`
    }
  }, [caseData])

  if (!caseId) {
    return null
  }

  return <CasePanelView caseId={caseId} />
}
