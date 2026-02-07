"use client"

import { Activity, Braces, MessageSquare, MoreHorizontal, Paperclip, X } from "lucide-react"
import { useRouter, useSearchParams } from "next/navigation"
import { useCallback, useMemo, useState } from "react"
import type {
  CaseDropdownDefinitionRead,
  CaseFieldRead,
  CasePriority,
  CaseSeverity,
  CaseStatus,
  CaseUpdate,
} from "@/client"
import { CaseAttachmentsSection } from "@/components/cases/case-attachments-section"
import { CommentSection } from "@/components/cases/case-comments-section"
import { CustomField } from "@/components/cases/case-panel-custom-fields"
import { CasePanelDescription } from "@/components/cases/case-panel-description"
import {
  type AssigneeInfo,
  AssigneeSelect,
  CaseDropdownSelect,
  PrioritySelect,
  SeveritySelect,
  StatusSelect,
} from "@/components/cases/case-panel-selectors"
import { CasePanelSummary } from "@/components/cases/case-panel-summary"
import { CasePayloadSection } from "@/components/cases/case-payload-section"
import { CaseTasksSection } from "@/components/cases/case-tasks-section"
import { CaseWorkflowTrigger } from "@/components/cases/case-workflow-trigger"
import { CaseFeed } from "@/components/cases/cases-feed"
import { AlertNotification } from "@/components/notifications"
import { TagBadge } from "@/components/tag-badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Separator } from "@/components/ui/separator"
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
} from "@/components/ui/sidebar"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useToast } from "@/components/ui/use-toast"
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import { useWorkspaceMembers } from "@/hooks/use-workspace"
import {
  useAddCaseTag,
  useCaseDropdownDefinitions,
  useCaseTagCatalog,
  useGetCase,
  useRemoveCaseTag,
  useSetCaseDropdownValue,
  useUpdateCase,
} from "@/lib/hooks"
import { undoSlugify } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

type CasePanelTab = "comments" | "activity" | "attachments" | "payload"

function isCustomFieldValueEmpty(value: unknown): boolean {
  if (value === null || value === undefined) return true
  if (typeof value === "string") return value.trim().length === 0
  if (typeof value === "number") return Number.isNaN(value)
  if (typeof value === "boolean") return false
  if (Array.isArray(value)) return value.length === 0
  if (typeof value === "object")
    return Object.keys(value as object).length === 0
  return false
}

interface CasePanelContentProps {
  caseId: string
}

export function CasePanelView({ caseId }: CasePanelContentProps) {
  const workspaceId = useWorkspaceId()
  const { members } = useWorkspaceMembers(workspaceId)
  const router = useRouter()
  const searchParams = useSearchParams()
  const { isFeatureEnabled } = useFeatureFlag()
  const caseTasksEnabled = isFeatureEnabled("case-tasks")
  const caseDropdownsEnabled = isFeatureEnabled("case-dropdowns")

  const { caseData, caseDataIsLoading, caseDataError } = useGetCase({
    caseId,
    workspaceId,
  })
  const { updateCase } = useUpdateCase({
    workspaceId,
    caseId,
  })
  const { addCaseTag } = useAddCaseTag({ caseId, workspaceId })
  const { removeCaseTag } = useRemoveCaseTag({ caseId, workspaceId })
  const { caseTags } = useCaseTagCatalog(workspaceId)
  const { dropdownDefinitions } = useCaseDropdownDefinitions(workspaceId)
  const setDropdownValue = useSetCaseDropdownValue(workspaceId)
  const { toast } = useToast()
  const customFields = useMemo(
    () => (caseData?.fields ?? []).filter((field) => !field.reserved),
    [caseData?.fields]
  )
  const [showAllCustomFields, setShowAllCustomFields] = useState(false)
  const visibleCustomFields = useMemo(
    () =>
      showAllCustomFields
        ? customFields
        : customFields.filter((field) => !isCustomFieldValueEmpty(field.value)),
    [customFields, showAllCustomFields]
  )
  const handleCustomFieldClear = useCallback(
    async (field: CaseFieldRead) => {
      try {
        await updateCase({
          fields: {
            [field.id]: null,
          },
        })
      } catch (error) {
        console.error("Failed to clear custom field:", error)
      }
    },
    [updateCase]
  )

  // Get active tab from URL query params, default to "comments"
  const activeTab = (
    searchParams &&
    ["comments", "activity", "attachments", "payload"].includes(
      searchParams.get("tab") || ""
    )
      ? (searchParams.get("tab") ?? "comments")
      : "comments"
  ) as CasePanelTab

  // Function to handle tab changes and update URL
  const handleTabChange = useCallback(
    (tab: string) => {
      router.push(`/workspaces/${workspaceId}/cases/${caseId}?tab=${tab}`)
    },
    [router, workspaceId, caseId]
  )

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
    await updateCase({ status: newStatus })
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

  const handleAssigneeChange = async (newAssignee?: AssigneeInfo | null) => {
    const params: Partial<CaseUpdate> = {
      assignee_id: newAssignee?.id || null,
    }
    await updateCase(params)
  }

  const handleTagToggle = async (tagId: string, hasTag: boolean) => {
    try {
      if (hasTag) {
        // Remove tag
        await removeCaseTag(tagId)
      } else {
        // Add tag
        await addCaseTag({ tag_id: tagId })
      }
    } catch (error) {
      console.error("Failed to modify tag:", error)
      toast({
        title: "Error",
        description: `Failed to ${hasTag ? "remove" : "add"} tag ${hasTag ? "from" : "to"} case. Please try again.`,
        variant: "destructive",
      })
    }
  }

  return (
    <>
      <CaseWorkflowTrigger caseData={caseData} />
      <div className="flex h-full w-full min-w-0">
        <div className="min-w-0 flex-1">
          <div className="h-full min-w-0 overflow-auto bg-muted/20">
            <div className="mx-auto max-w-4xl px-6 py-8 pb-24">
              <div className="mb-2">
                <div className="flex flex-col">
                  <div className="py-1.5 first:pt-0 last:pb-0">
                    <CasePanelSummary caseData={caseData} updateCase={updateCase} />
                  </div>
                  <div className="flex flex-wrap items-center justify-between gap-3 py-1.5 first:pt-0 last:pb-0">
                    <div className="flex flex-wrap items-center gap-1.5">
                      {caseData.tags?.length ? (
                        caseData.tags.map((tag) => (
                          <TagBadge key={tag.id} tag={tag} />
                        ))
                      ) : (
                        <span className="text-xs text-muted-foreground">
                          No tags
                        </span>
                      )}
                    </div>
                    {caseTags && caseTags.length > 0 && (
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="sm" className="h-6 w-6 p-0">
                            <MoreHorizontal className="h-4 w-4" />
                            <span className="sr-only">Manage tags</span>
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end" className="text-xs">
                          {caseTags.map((tag) => {
                            const hasTag = caseData.tags?.some(
                              (t) => t.id === tag.id
                            )
                            return (
                              <DropdownMenuCheckboxItem
                                key={tag.id}
                                className="text-xs"
                                checked={hasTag}
                                onClick={async (e) => {
                                  e.stopPropagation()
                                  await handleTagToggle(tag.id, !!hasTag)
                                }}
                              >
                                <div
                                  className="mr-2 flex size-2 rounded-full"
                                  style={{
                                    backgroundColor: tag.color || undefined,
                                  }}
                                />
                                <span>{tag.name}</span>
                              </DropdownMenuCheckboxItem>
                            )
                          })}
                        </DropdownMenuContent>
                      </DropdownMenu>
                    )}
                  </div>
                </div>
              </div>

              <div className="mb-4">
                <CasePanelDescription caseData={caseData} updateCase={updateCase} />
              </div>

              {caseTasksEnabled && (
                <div className="mb-6">
                  <CaseTasksSection
                    caseId={caseId}
                    workspaceId={workspaceId}
                    caseData={caseData}
                  />
                </div>
              )}

              <Tabs
                value={activeTab}
                onValueChange={handleTabChange}
                className="mt-[4.5rem] w-full"
              >
                <TabsList className="h-8 w-full justify-start rounded-none bg-transparent p-0">
                  <TabsTrigger
                    className="flex h-full items-center justify-center rounded-none py-0 text-xs font-medium data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                    value="comments"
                  >
                    <MessageSquare className="mr-1.5 h-3.5 w-3.5" />
                    Comments
                  </TabsTrigger>
                  <TabsTrigger
                    className="ml-6 flex h-full items-center justify-center rounded-none py-0 text-xs font-medium data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                    value="activity"
                  >
                    <Activity className="mr-1.5 h-3.5 w-3.5" />
                    Activity
                  </TabsTrigger>
                  <TabsTrigger
                    className="ml-6 flex h-full items-center justify-center rounded-none py-0 text-xs font-medium data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                    value="attachments"
                  >
                    <Paperclip className="mr-1.5 h-3.5 w-3.5" />
                    Attachments
                  </TabsTrigger>
                  <TabsTrigger
                    className="ml-6 flex h-full items-center justify-center rounded-none py-0 text-xs font-medium data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                    value="payload"
                  >
                    <Braces className="mr-1.5 h-3.5 w-3.5" />
                    Payload
                  </TabsTrigger>
                </TabsList>
                <Separator className="mt-0" />

                <TabsContent value="comments" className="mt-4">
                  <CommentSection caseId={caseId} workspaceId={workspaceId} />
                </TabsContent>

                <TabsContent value="activity" className="mt-4">
                  <CaseFeed caseId={caseId} workspaceId={workspaceId} />
                </TabsContent>

                <TabsContent value="attachments" className="mt-4">
                  <CaseAttachmentsSection
                    caseId={caseId}
                    workspaceId={workspaceId}
                  />
                </TabsContent>

                <TabsContent value="payload" className="mt-4">
                  <CasePayloadSection caseData={caseData} />
                </TabsContent>
              </Tabs>
            </div>
          </div>
        </div>
        <Sidebar
          side="right"
          collapsible="none"
          className="w-[22rem] shrink-0 border-l border-border bg-background text-foreground"
        >
          <SidebarContent className="h-full">
            <SidebarGroup>
              <SidebarGroupLabel>Properties</SidebarGroupLabel>
              <SidebarGroupContent className="px-2">
                <div className="space-y-2">
                  <div className="flex h-7 w-full items-center gap-2">
                    <span className="text-sm text-muted-foreground">Status</span>
                    <div className="ml-auto min-w-0 flex-1">
                      <StatusSelect
                        status={caseData.status}
                        onValueChange={handleStatusChange}
                        showLabel={false}
                        triggerClassName="h-7 w-full justify-end px-2 text-sm [&>span]:w-full"
                        valueClassName="text-sm"
                      />
                    </div>
                  </div>
                  <div className="flex h-7 w-full items-center gap-2">
                    <span className="text-sm text-muted-foreground">Priority</span>
                    <div className="ml-auto min-w-0 flex-1">
                      <PrioritySelect
                        priority={caseData.priority || "unknown"}
                        onValueChange={handlePriorityChange}
                        showLabel={false}
                        triggerClassName="h-7 w-full justify-end px-2 text-sm [&>span]:w-full"
                        valueClassName="text-sm"
                      />
                    </div>
                  </div>
                  <div className="flex h-7 w-full items-center gap-2">
                    <span className="text-sm text-muted-foreground">Severity</span>
                    <div className="ml-auto min-w-0 flex-1">
                      <SeveritySelect
                        severity={caseData.severity || "unknown"}
                        onValueChange={handleSeverityChange}
                        showLabel={false}
                        triggerClassName="h-7 w-full justify-end px-2 text-sm [&>span]:w-full"
                        valueClassName="text-sm"
                      />
                    </div>
                  </div>
                  <div className="flex h-7 w-full items-center gap-2">
                    <span className="text-sm text-muted-foreground">Assignee</span>
                    <div className="ml-auto min-w-0 flex-1">
                      <AssigneeSelect
                        assignee={caseData.assignee}
                        workspaceMembers={members ?? []}
                        onValueChange={handleAssigneeChange}
                        showLabel={false}
                        triggerClassName="h-7 w-full justify-end px-2 text-sm [&>span]:w-full"
                        valueClassName="text-sm"
                      />
                    </div>
                  </div>
                  {caseDropdownsEnabled &&
                    dropdownDefinitions?.map((def: CaseDropdownDefinitionRead) => {
                      const currentValue = caseData.dropdown_values?.find(
                        (dv) => dv.definition_id === def.id
                      )
                      return (
                        <div key={def.id} className="flex h-7 w-full items-center gap-2">
                          <span
                            className="truncate text-sm text-muted-foreground"
                            title={def.name}
                          >
                            {def.name}
                          </span>
                          <div className="ml-auto min-w-0 flex-1">
                            <CaseDropdownSelect
                              definition={def}
                              currentValue={currentValue}
                              onValueChange={(optionId) =>
                                setDropdownValue.mutate({
                                  caseId: caseData.id,
                                  definitionId: def.id,
                                  optionId,
                                })
                              }
                              showLabel={false}
                              triggerClassName="h-7 w-full justify-end px-2 text-sm [&>span]:w-full"
                              valueClassName="text-sm"
                            />
                          </div>
                        </div>
                      )
                    })}
                </div>
              </SidebarGroupContent>
            </SidebarGroup>
            <SidebarGroup>
              <SidebarGroupLabel>Fields</SidebarGroupLabel>
              <SidebarGroupContent className="px-2">
                <div className="space-y-2">
                  {visibleCustomFields.length > 0 ? (
                    visibleCustomFields.map((field) => {
                      const label = undoSlugify(field.id)
                      return (
                        <div key={field.id} className="flex h-7 w-full items-center gap-2">
                          {showAllCustomFields && (
                            <Button
                              variant="ghost"
                              size="icon"
                              type="button"
                              className="h-7 w-7 shrink-0 text-muted-foreground hover:text-foreground"
                              onClick={() => handleCustomFieldClear(field)}
                            >
                              <X className="h-3.5 w-3.5" />
                              <span className="sr-only">Clear {label} field</span>
                            </Button>
                          )}
                          <span
                            className="truncate text-sm text-muted-foreground"
                            title={label}
                          >
                            {label}
                          </span>
                          <div className="ml-auto min-w-0 flex-1">
                            <div className="flex h-7 w-full items-center gap-2">
                              <div className="min-w-0 flex-1">
                                <CustomField
                                  customField={field}
                                  updateCase={updateCase}
                                  formClassName="w-full"
                                  inputClassName="w-full min-w-0 text-sm"
                                />
                              </div>
                            </div>
                          </div>
                        </div>
                      )
                    })
                  ) : customFields.length === 0 ? (
                    <span className="text-sm text-muted-foreground">
                      No custom fields configured
                    </span>
                  ) : null}
                  {customFields.length > 0 && (
                    <button
                      type="button"
                      className="h-7 text-sm text-muted-foreground underline-offset-4 hover:underline"
                      onClick={() => setShowAllCustomFields((prev) => !prev)}
                    >
                      {showAllCustomFields ? "Hide empty fields" : "View all fields"}
                    </button>
                  )}
                </div>
              </SidebarGroupContent>
            </SidebarGroup>
          </SidebarContent>
        </Sidebar>
      </div>
    </>
  )
}
