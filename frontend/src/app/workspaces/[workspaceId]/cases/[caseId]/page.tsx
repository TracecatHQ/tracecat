"use client"

import { useParams } from "next/navigation"

import { CasePanelView } from "@/components/cases/case-panel-view"

export default function CaseDetailPage() {
  const params = useParams<{ caseId: string }>()
  const caseId = params?.caseId

  if (!caseId) {
    return null
  }

  return <CasePanelView caseId={caseId} />
}
