"use client"

import {
  Activity,
  Braces,
  MessageSquare,
  MessageSquareText,
  MoreHorizontal,
  Paperclip,
} from "lucide-react"
import Link from "next/link"
import { useEffect, useState } from "react"
import type {
  CasePriority,
  CaseSeverity,
  CaseStatus,
  CaseUpdate,
  UserRead,
} from "@/client"
import { CaseActivityFeed } from "@/components/cases/case-activity-feed"
import { CaseAttachmentsSection } from "@/components/cases/case-attachments-section"
import { CaseChat } from "@/components/cases/case-chat"
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
import { CasePayloadSection } from "@/components/cases/case-payload-section"
import { CasePropertyRow } from "@/components/cases/case-property-row"
import { CaseWorkflowTrigger } from "@/components/cases/case-workflow-trigger"
import { AlertNotification } from "@/components/notifications"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable"
import { Skeleton } from "@/components/ui/skeleton"
import { useGetCase, useUpdateCase } from "@/lib/hooks"
import { useWorkspace } from "@/providers/workspace"

interface CasePanelContentProps {
  caseId: string
  onChatToggle?: (isOpen: boolean) => void
  isChatOpen?: boolean
}

export function CasePanelView({
  caseId,
  onChatToggle,
  isChatOpen: externalChatOpen,
}: CasePanelContentProps) {
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
  const [activeTab, setActiveTab] = useState<
    "comments" | "activity" | "attachments" | "payload"
  >("comments")

  // Chat state management
  const [localChatOpen, setLocalChatOpen] = useState(true)
  const isChatOpen =
    externalChatOpen !== undefined ? externalChatOpen : localChatOpen

  // Load chat panel state from localStorage
  useEffect(() => {
    const savedState = localStorage.getItem(`case-chat-panel-${caseId}`)
    if (savedState === "true") {
      setLocalChatOpen(true)
      onChatToggle?.(true)
    }
  }, [caseId, onChatToggle])

  // Save chat panel state to localStorage
  useEffect(() => {
    localStorage.setItem(`case-chat-panel-${caseId}`, isChatOpen.toString())
  }, [caseId, isChatOpen])

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

  const toggleChat = () => {
    const newState = !isChatOpen
    setLocalChatOpen(newState)
    onChatToggle?.(newState)
  }

  return (
    <div className="h-screen bg-background flex flex-col">
      {/* Main Content */}
      <ResizablePanelGroup direction="horizontal" className="flex-1">
        {/* Case properties section */}
        <ResizablePanel defaultSize={10} minSize={8} maxSize={20}>
          <div className="h-full border-r flex flex-col">
            <div className="flex-1 overflow-y-auto p-4">
              <div className="space-y-10">
                {/* Properties Section */}
                <CasePanelSection
                  title="Properties"
                  isOpen={propertiesOpen}
                  onOpenChange={setPropertiesOpen}
                >
                  <div className="space-y-4">
                    {/* Assign */}
                    <CasePropertyRow
                      label="Assignee"
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
                                href={`/workspaces/${workspaceId}/custom-fields`}
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
        </ResizablePanel>

        <ResizableHandle className="bg-transparent" />

        {/* Main section */}
        <ResizablePanel defaultSize={isChatOpen ? 55 : 80}>
          <div className="h-full flex flex-col overflow-hidden items-center">
            <div className="flex-1 overflow-y-auto py-4 px-6 min-w-[800px]">
              <div className="max-w-4xl">
                {/* Header with Chat Toggle */}
                <div className="flex items-center justify-between mb-4">
                  <div className="flex-1">
                    {/* Case Summary */}
                    <CasePanelSummary
                      caseData={caseData}
                      updateCase={updateCase}
                    />
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={toggleChat}
                    className="ml-4"
                  >
                    <MessageSquareText className="h-4 w-4" />
                  </Button>
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
                    <button
                      onClick={() => setActiveTab("payload")}
                      className={`flex items-center gap-1.5 px-0.5 py-2 ml-6 text-xs font-medium border-b-2 transition-colors ${
                        activeTab === "payload"
                          ? "border-foreground text-foreground"
                          : "border-transparent text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      <Braces className="h-3.5 w-3.5" />
                      Payload
                    </button>
                  </div>

                  <div className="mt-4">
                    {activeTab === "comments" && (
                      <CommentSection
                        caseId={caseId}
                        workspaceId={workspaceId}
                      />
                    )}

                    {activeTab === "activity" && (
                      <CaseActivityFeed
                        caseId={caseId}
                        workspaceId={workspaceId}
                      />
                    )}

                    {activeTab === "attachments" && (
                      <CaseAttachmentsSection
                        caseId={caseId}
                        workspaceId={workspaceId}
                      />
                    )}

                    {activeTab === "payload" && (
                      <CasePayloadSection caseData={caseData} />
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </ResizablePanel>

        {/* Chat section */}
        {isChatOpen && (
          <>
            <ResizableHandle className="bg-transparent" />
            <ResizablePanel defaultSize={20} minSize={10} maxSize={50}>
              <CaseChat caseId={caseId} isChatOpen={isChatOpen} />
            </ResizablePanel>
          </>
        )}
      </ResizablePanelGroup>
    </div>
  )
}
