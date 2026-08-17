"use client"

import type { CaseRead, CaseUpdate } from "@/client"
import { CaseAttachmentsSection } from "@/components/cases/case-attachments-section"
import { CaseLinkedRowsSection } from "@/components/cases/case-linked-rows-section"
import { CasePanelDescription } from "@/components/cases/case-panel-description"
import type { CasePanelKey } from "@/components/cases/case-panels"
import { CasePayloadSection } from "@/components/cases/case-payload-section"
import { CaseTasksPanel } from "@/components/cases/case-tasks-panel"
import { CaseFeed } from "@/components/cases/cases-feed"

/** Props for {@link CasePanelContent}. */
export interface CasePanelContentProps {
  /** Panel to render. The view resolves entitlement-hidden keys first. */
  panel: CasePanelKey
  caseId: string
  workspaceId: string
  caseData: CaseRead
  updateCase: (caseData: CaseUpdate) => Promise<void>
  /** Embedded (chat artifact) density, forwarded to panels that support it. */
  embedded?: boolean
}

/**
 * Maps the active switcher key to its panel section. Extracted from
 * `case-panel-view.tsx` so the view stays focused on layout and state.
 * Inactive panels are unmounted, not hidden — see the mounting resolution in
 * the case details redesign plan.
 */
export function CasePanelContent({
  panel,
  caseId,
  workspaceId,
  caseData,
  updateCase,
  embedded = false,
}: CasePanelContentProps) {
  switch (panel) {
    case "description":
      return (
        <CasePanelDescription
          caseData={caseData}
          updateCase={updateCase}
          compact={embedded}
        />
      )
    case "tasks":
      return (
        <CaseTasksPanel
          caseId={caseId}
          workspaceId={workspaceId}
          caseData={caseData}
        />
      )
    case "attachments":
      return (
        <CaseAttachmentsSection caseId={caseId} workspaceId={workspaceId} />
      )
    case "rows":
      return <CaseLinkedRowsSection caseId={caseId} workspaceId={workspaceId} />
    case "payload":
      return <CasePayloadSection caseData={caseData} />
    case "activity":
      return <CaseFeed caseId={caseId} workspaceId={workspaceId} />
  }
}
