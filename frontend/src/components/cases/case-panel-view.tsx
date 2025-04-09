"use client"

import React from "react"
import { CasePriority, CaseSeverity, CaseStatus, CaseUpdate } from "@/client"
import { useWorkspace } from "@/providers/workspace"

import { useGetCase, useUpdateCase } from "@/lib/hooks"
import { Badge } from "@/components/ui/badge"
import { CommentSection } from "@/components/cases/case-comments-section"
import { CustomField } from "@/components/cases/case-panel-custom-fields"
import { CasePanelDescription } from "@/components/cases/case-panel-description"
import {
  PrioritySelect,
  SeveritySelect,
  StatusSelect,
} from "@/components/cases/case-panel-selectors"
import { CasePanelSummary } from "@/components/cases/case-panel-summary"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"

// Form schemas
interface CasePanelContentProps {
  caseId: string
}

export function CasePanelView({ caseId }: CasePanelContentProps) {
  const { workspaceId } = useWorkspace()
  const { caseData, caseDataIsLoading, caseDataError } = useGetCase({
    caseId,
    workspaceId,
  })
  const { updateCase } = useUpdateCase({
    workspaceId,
    caseId,
  })

  if (caseDataIsLoading) {
    return <CenteredSpinner />
  }
  if (caseDataError || !caseData) {
    return (
      <AlertNotification
        level="error"
        message={caseDataError?.message ?? "Error occurred loading case data"}
      />
    )
  }

  const handleStatusChange = async (newStatus: CaseStatus) => {
    const updateParams = {
      status: newStatus,
    } as Partial<CaseUpdate>
    await updateCase(updateParams)
  }

  const handlePriorityChange = async (newPriority: CasePriority) => {
    const params = {
      priority: newPriority,
    }
    await updateCase(params)
  }

  const handleSeverityChange = async (newSeverity: CaseSeverity) => {
    const params = {
      severity: newSeverity,
    }
    await updateCase(params)
  }

  const customFields = caseData.fields.filter((field) => !field.reserved)
  return (
    <div className="flex h-full flex-col overflow-auto px-6">
      <div className="flex items-center justify-between border-b p-4">
        <div className="flex items-center space-x-2">
          <Badge variant="outline" className="text-xs font-medium">
            {caseData.short_id}
          </Badge>
        </div>
      </div>

      <div className="grid grid-cols-8 gap-6 overflow-visible p-6">
        {/* Left column - Summary & Description */}
        <div className="col-span-5 space-y-6">
          {/* Summary */}
          <div className="space-y-2">
            <CasePanelSummary caseData={caseData} updateCase={updateCase} />
          </div>

          {/* Description */}
          <div className="space-y-2">
            <div className="space-y-2">
              <CasePanelDescription
                caseData={caseData}
                updateCase={updateCase}
              />
            </div>
          </div>

          <CommentSection caseId={caseId} workspaceId={workspaceId} />
        </div>

        {/* Right column - Details & Custom Fields */}
        <div className="col-span-3 space-y-6">
          <div className="bg-card p-4">
            <h3 className="mb-3 text-sm font-semibold text-muted-foreground">
              Details
            </h3>
            <div className="space-y-4">
              {/* Status */}
              <div className="grid grid-cols-3 items-center">
                <span className="text-sm font-medium text-muted-foreground">
                  Status
                </span>
                <div className="col-span-2">
                  <StatusSelect
                    status={caseData.status}
                    onValueChange={handleStatusChange}
                  />
                </div>
              </div>

              {/* Priority */}
              <div className="grid grid-cols-3 items-center">
                <span className="text-sm font-medium text-muted-foreground">
                  Priority
                </span>
                <div className="col-span-2">
                  <PrioritySelect
                    priority={caseData.priority || "medium"}
                    onValueChange={handlePriorityChange}
                  />
                </div>
              </div>

              {/* Severity */}
              <div className="grid grid-cols-3 items-center">
                <span className="text-sm font-medium text-muted-foreground">
                  Severity
                </span>
                <div className="col-span-2">
                  <SeveritySelect
                    severity={caseData.severity || "medium"}
                    onValueChange={handleSeverityChange}
                  />
                </div>
              </div>
            </div>
          </div>

          {/* Custom Fields */}
          <div className="bg-card p-4">
            <h3 className="mb-3 text-sm font-semibold text-muted-foreground">
              Custom Fields
            </h3>
            {customFields.length === 0 ? (
              <div className="py-4 text-center text-xs text-muted-foreground">
                No custom fields have been added yet
              </div>
            ) : (
              <div className="space-y-4">
                {customFields.map((field) => (
                  <div key={field.id} className="grid grid-cols-3 items-center">
                    <span className="text-sm font-medium text-muted-foreground">
                      {field.id}
                    </span>
                    <div className="col-span-2">
                      <CustomField customField={field} />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
