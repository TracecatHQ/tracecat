"use client"

import React from "react"
import {
  CasePriority,
  CaseSeverity,
  CaseStatus,
  CaseUpdate,
  UserRead,
} from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { format, formatDistanceToNow } from "date-fns"
import {
  Activity,
  ActivityIcon,
  Braces,
  List,
  MessageCircleIcon,
  Paperclip,
  PaperclipIcon,
  PlayCircle,
  Plus,
  UserCircle2,
} from "lucide-react"

import { useGetCase, useUpdateCase } from "@/lib/hooks"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { CommentSection } from "@/components/cases/case-comments-section"
import { CustomField } from "@/components/cases/case-panel-custom-fields"
import { CasePanelDescription } from "@/components/cases/case-panel-description"
import {
  AssigneeSelect,
  PrioritySelect,
  SeveritySelect,
  StatusSelect,
} from "@/components/cases/case-panel-selectors"
import { CasePanelSummary } from "@/components/cases/case-panel-summary"
import { CaseWorkflowTrigger } from "@/components/cases/case-workflow-trigger"
import { AlertNotification } from "@/components/notifications"

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

  if (caseDataIsLoading) {
    return (
      <div className="flex h-full flex-col space-y-4 p-4">
        <div className="flex items-center justify-between border-b p-4">
          <div className="flex items-center space-x-4">
            <Skeleton className="h-6 w-20" />
            <div className="flex items-center space-x-2">
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-4 w-40" />
            </div>
          </div>
        </div>
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-[250px] w-full" />
        <div className="flex space-x-4">
          <Skeleton className="h-8 w-24" />
          <Skeleton className="h-8 w-24" />
          <Skeleton className="h-8 w-24" />
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
    <div className="flex h-full flex-col overflow-auto px-6">
      <div className="flex items-center justify-between border-b p-4">
        <div className="flex items-center space-x-4">
          <Badge variant="outline" className="text-xs font-medium">
            {caseData.short_id}
          </Badge>
          <div className="flex items-center space-x-2 text-xs text-muted-foreground">
            <span>Created {format(new Date(caseData.created_at), "PPpp")}</span>
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

      <div className="grid grid-cols-8 gap-6 overflow-visible p-6">
        {/* Left column - Summary & Description */}
        <div className="col-span-5 space-y-6">
          {/* Summary */}
          <div className="space-y-2">
            <CasePanelSummary caseData={caseData} updateCase={updateCase} />
          </div>

          {/* Description */}
          <div className="space-y-2">
            <CasePanelDescription caseData={caseData} updateCase={updateCase} />
          </div>
          <Tabs defaultValue="comments" className="w-full">
            <div className="w-full border-b">
              <TabsList className="h-8 justify-start rounded-none bg-transparent p-0">
                <TabsTrigger
                  className="flex h-full min-w-24 items-center justify-center rounded-none border-b-2 border-transparent py-0 text-xs data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                  value="comments"
                >
                  <MessageCircleIcon className="mr-2 size-4" />
                  <span>Comments</span>
                </TabsTrigger>
                <TabsTrigger
                  className="flex h-full min-w-24 items-center justify-center rounded-none border-b-2 border-transparent py-0 text-xs data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                  value="activity"
                >
                  <ActivityIcon className="mr-2 size-4" />
                  <span>Activity</span>
                </TabsTrigger>
                <TabsTrigger
                  className="flex h-full min-w-24 items-center justify-center rounded-none border-b-2 border-transparent py-0 text-xs data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                  value="attachments"
                >
                  <PaperclipIcon className="mr-2 size-4" />
                  <span>Attachments</span>
                </TabsTrigger>
              </TabsList>
            </div>
            <TabsContent value="comments" className="p-4">
              <CommentSection caseId={caseId} workspaceId={workspaceId} />
            </TabsContent>
            <TabsContent value="activity" className="p-4">
              <div className="flex size-full flex-col items-center justify-center">
                <div className="flex flex-col items-center gap-4 p-6 text-center">
                  <div className="rounded-full bg-muted p-3">
                    <Activity className="size-6 text-muted-foreground" />
                  </div>
                  <div className="space-y-1">
                    <h4 className="text-sm font-semibold">No activity yet</h4>
                    <p className="text-xs text-muted-foreground">
                      Activity will be shown here as changes are made to the
                      case
                    </p>
                  </div>
                </div>
              </div>
            </TabsContent>
            <TabsContent value="attachments" className="p-4">
              <div className="flex size-full flex-col items-center justify-center">
                <div className="flex flex-col items-center gap-4 p-6 text-center">
                  <div className="rounded-full bg-muted p-3">
                    <Paperclip className="size-6 text-muted-foreground" />
                  </div>
                  <div className="space-y-1">
                    <h4 className="text-sm font-semibold">
                      No attachments yet
                    </h4>
                    <p className="text-xs text-muted-foreground">
                      Get started by adding your first attachment
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    className="w-full gap-1.5"
                    disabled
                  >
                    <Plus className="size-4" />
                    Add Attachment
                  </Button>
                </div>
              </div>
            </TabsContent>
          </Tabs>
        </div>

        {/* Right column - Details & Custom Fields */}
        <div className="col-span-3 space-y-6">
          <div className="bg-card p-4">
            <h3 className="mb-3 flex items-center text-sm font-semibold text-muted-foreground">
              <UserCircle2 className="mr-2 size-4" />
              Assigned To
            </h3>
            <div className="space-y-4">
              <AssigneeSelect
                assignee={caseData.assignee}
                workspaceMembers={workspace?.members ?? []}
                onValueChange={handleAssigneeChange}
              />
            </div>
          </div>
          <div className="bg-card p-4">
            <h3 className="mb-3 flex items-center text-sm font-semibold text-muted-foreground">
              <List className="mr-2 size-4" />
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
            <h3 className="mb-3 flex items-center text-sm font-semibold text-muted-foreground">
              <Braces className="mr-2 size-4" strokeWidth={1.5} />
              Custom Fields
            </h3>
            {customFields.length === 0 ? (
              <div className="py-4 text-center text-xs text-muted-foreground">
                No custom fields have been added yet
              </div>
            ) : (
              <div className="min-h-20 space-y-4">
                {customFields.map((field) => (
                  <div key={field.id} className="grid grid-cols-3 items-center">
                    <span className="text-sm font-medium text-muted-foreground">
                      {field.id}
                    </span>
                    <div className="col-span-2">
                      <CustomField
                        customField={field}
                        updateCase={updateCase}
                      />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Workflow Trigger */}
          <div className="bg-card p-4">
            <h3 className="mb-3 flex items-center text-sm font-semibold text-muted-foreground">
              <PlayCircle className="mr-2 size-4" />
              Trigger Workflow
            </h3>
            <CaseWorkflowTrigger caseData={caseData} />
          </div>
        </div>
      </div>
    </div>
  )
}
