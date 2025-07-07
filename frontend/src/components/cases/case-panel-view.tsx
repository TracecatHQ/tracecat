"use client"

import { format, formatDistanceToNow } from "date-fns"
import {
  Activity,
  ArrowLeft,
  Calendar,
  MessageSquare,
  MoreHorizontal,
  Paperclip,
} from "lucide-react"
import Link from "next/link"
import { useState } from "react"
import type {
  CasePriority,
  CaseSeverity,
  CaseStatus,
  CaseUpdate,
  UserRead,
} from "@/client"
import { CaseActivityFeed } from "@/components/cases/case-activity-feed"
import { CaseAttachmentsSection } from "@/components/cases/case-attachments-section"
import { CommentSection } from "@/components/cases/case-comments-section"
import { CustomField } from "@/components/cases/case-panel-custom-fields"
import { CasePanelDescription } from "@/components/cases/case-panel-description"
import { CasePanelSection } from "@/components/cases/case-panel-section"
import {
  AssigneeSelect,
  PrioritySelect,
  SeveritySelect,
  StatusSelect,
} from "@/components/cases/case-panel-selectors"
import { CasePanelSummary } from "@/components/cases/case-panel-summary"
import { CasePropertyRow } from "@/components/cases/case-property-row"
import { CaseWorkflowTrigger } from "@/components/cases/case-workflow-trigger"
import { AlertNotification } from "@/components/notifications"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Skeleton } from "@/components/ui/skeleton"
import { useGetCase, useUpdateCase } from "@/lib/hooks"
import { useWorkspace } from "@/providers/workspace"

interface CasePanelContentProps {
  caseId: string
}

export function CasePanelView({ caseId }: CasePanelContentProps) {
  const { workspaceId, workspace } = useWorkspace()
  const { caseData, caseDataIsLoading, caseDataError } = useGetCase({
    caseId,
    workspaceId,
  })
  const { updateCase } = useUpdateCase({
    workspaceId,
    caseId,
  })
  const [propertiesOpen, setPropertiesOpen] = useState(true)
  const [workflowOpen, setWorkflowOpen] = useState(true)
  const [activeTab, setActiveTab] = useState("comments")

  if (caseDataIsLoading) {
    return (
      <div className="flex h-full flex-col space-y-4 p-4">
        <div className="flex items-center justify-between border-b p-4">
          <div className="flex items-center space-x-4">
            <Skeleton className="h-4 w-16" />
            <div className="flex items-center space-x-2">
              <Skeleton className="h-3 w-32" />
              <Skeleton className="h-3 w-32" />
            </div>
          </div>
        </div>
        <Skeleton className="h-6 w-full" />
        <Skeleton className="h-[200px] w-full" />
        <div className="flex space-x-4">
          <Skeleton className="h-6 w-20" />
          <Skeleton className="h-6 w-20" />
          <Skeleton className="h-6 w-20" />
        </div>
      </div>
    )
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

  const handleAssigneeChange = async (newAssignee?: UserRead | null) => {
    const params: Partial<CaseUpdate> = {
      assignee_id: newAssignee?.id || null,
    }
    await updateCase(params)
  }

  const customFields = caseData.fields.filter((field) => !field.reserved)

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <div className="border-b">
        <div className="flex h-11 items-center px-4">
          <Button
            variant="ghost"
            size="sm"
            className="mr-1 h-7 w-7 p-0"
            asChild
          >
            <Link href={`/workspaces/${workspaceId}/cases`}>
              <ArrowLeft className="h-3.5 w-3.5" />
            </Link>
          </Button>
          <Breadcrumb>
            <BreadcrumbList className="text-xs">
              <BreadcrumbItem>
                <BreadcrumbLink href={`/workspaces/${workspaceId}/cases`}>
                  Cases
                </BreadcrumbLink>
              </BreadcrumbItem>
              <BreadcrumbSeparator />
              <BreadcrumbItem>
                <BreadcrumbPage>{caseData.short_id}</BreadcrumbPage>
              </BreadcrumbItem>
            </BreadcrumbList>
          </Breadcrumb>
          <div className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
            <span className="flex items-center gap-1">
              <Calendar className="h-3 w-3" />
              Created{" "}
              {format(new Date(caseData.created_at), "MMM d, yyyy, h:mm a")}
            </span>
            <span>•</span>
            <span>
              Updated{" "}
              {formatDistanceToNow(new Date(caseData.updated_at), {
                addSuffix: true,
              })}
            </span>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex">
        {/* Left Panel */}
        <div className="flex-1 p-4">
          <div className="max-w-4xl">
            {/* Case Header */}
            <div className="mb-4">
              <CasePanelSummary caseData={caseData} updateCase={updateCase} />
            </div>

            {/* Description */}
            <div className="mb-6">
              <CasePanelDescription
                caseData={caseData}
                updateCase={updateCase}
              />
            </div>

            {/* Tabs - Clean underline style */}
            <div className="w-full">
              <div className="flex border-b">
                <button
                  onClick={() => setActiveTab("comments")}
                  className={`flex items-center gap-1.5 px-0.5 py-2 text-xs font-medium border-b-2 transition-colors ${
                    activeTab === "comments"
                      ? "border-foreground text-foreground"
                      : "border-transparent text-muted-foreground hover:text-foreground"
                  }`}
                >
                  <MessageSquare className="h-3.5 w-3.5" />
                  Comments
                </button>
                <button
                  onClick={() => setActiveTab("activity")}
                  className={`flex items-center gap-1.5 px-0.5 py-2 ml-6 text-xs font-medium border-b-2 transition-colors ${
                    activeTab === "activity"
                      ? "border-foreground text-foreground"
                      : "border-transparent text-muted-foreground hover:text-foreground"
                  }`}
                >
                  <Activity className="h-3.5 w-3.5" />
                  Activity
                </button>
                <button
                  onClick={() => setActiveTab("attachments")}
                  className={`flex items-center gap-1.5 px-0.5 py-2 ml-6 text-xs font-medium border-b-2 transition-colors ${
                    activeTab === "attachments"
                      ? "border-foreground text-foreground"
                      : "border-transparent text-muted-foreground hover:text-foreground"
                  }`}
                >
                  <Paperclip className="h-3.5 w-3.5" />
                  Attachments
                </button>
              </div>

              <div className="mt-4">
                {activeTab === "comments" && (
                  <CommentSection caseId={caseId} workspaceId={workspaceId} />
                )}

                {activeTab === "activity" && (
                  <CaseActivityFeed caseId={caseId} workspaceId={workspaceId} />
                )}

                {activeTab === "attachments" && (
                  <CaseAttachmentsSection
                    caseId={caseId}
                    workspaceId={workspaceId}
                  />
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Right Panel */}
        <div className="w-72 border-l p-4">
          <div className="space-y-10">
            {/* Properties Section */}
            <CasePanelSection
              title="Properties"
              isOpen={propertiesOpen}
              onOpenChange={setPropertiesOpen}
            >
              <div className="space-y-4">
                {/* Assigned To */}
                <CasePropertyRow
                  label="Assigned To"
                  value={
                    <AssigneeSelect
                      assignee={caseData.assignee}
                      workspaceMembers={workspace?.members ?? []}
                      onValueChange={handleAssigneeChange}
                    />
                  }
                />

                {/* Status */}
                <CasePropertyRow
                  label="Status"
                  value={
                    <StatusSelect
                      status={caseData.status}
                      onValueChange={handleStatusChange}
                    />
                  }
                />

                {/* Priority */}
                <CasePropertyRow
                  label="Priority"
                  value={
                    <PrioritySelect
                      priority={caseData.priority || "unknown"}
                      onValueChange={handlePriorityChange}
                    />
                  }
                />

                {/* Severity */}
                <CasePropertyRow
                  label="Severity"
                  value={
                    <SeveritySelect
                      severity={caseData.severity || "unknown"}
                      onValueChange={handleSeverityChange}
                    />
                  }
                />

                {/* Custom fields */}
                <div className="pt-2">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs">Custom fields</span>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-5 w-5 p-0"
                        >
                          <MoreHorizontal className="h-3 w-3" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="text-xs">
                        <DropdownMenuItem asChild>
                          <Link
                            href={`/workspaces/${workspaceId}/settings/custom-fields`}
                          >
                            Manage fields
                          </Link>
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                  {customFields.length === 0 ? (
                    <div className="text-xs text-muted-foreground">
                      No fields configured.
                    </div>
                  ) : (
                    <div className="space-y-4">
                      {customFields.map((field) => (
                        <CasePropertyRow
                          key={field.id}
                          label={field.id}
                          value={
                            <CustomField
                              customField={field}
                              updateCase={updateCase}
                            />
                          }
                        />
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </CasePanelSection>
            {/* Workflow Triggers */}
            <CasePanelSection
              title="Workflows"
              isOpen={workflowOpen}
              onOpenChange={setWorkflowOpen}
            >
              <CaseWorkflowTrigger caseData={caseData} />
            </CasePanelSection>
          </div>
        </div>
      </div>
    </div>
  )
}
