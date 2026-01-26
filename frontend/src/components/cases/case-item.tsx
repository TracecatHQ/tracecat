"use client"

import { useQueryClient } from "@tanstack/react-query"
import {
  Check,
  Copy,
  ExternalLink,
  ShieldAlertIcon,
  SignalHighIcon,
  SignalIcon,
  TagsIcon,
  Trash2,
  UserIcon,
} from "lucide-react"
import Link from "next/link"
import { useEffect, useState } from "react"
import type {
  CasePriority,
  CaseReadMinimal,
  CaseSeverity,
  CaseStatus,
  CaseTagRead,
  WorkspaceMember,
} from "@/client"
import { casesAddTag, casesRemoveTag, casesUpdateCase } from "@/client"
import { CaseBadge } from "@/components/cases/case-badge"
import {
  PRIORITIES,
  SEVERITIES,
  STATUSES,
} from "@/components/cases/case-categories"
import { UNASSIGNED } from "@/components/cases/case-panel-selectors"
import {
  EventCreatedAt,
  EventUpdatedAt,
} from "@/components/cases/cases-feed-event"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import {
  ContextMenu,
  ContextMenuCheckboxItem,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuRadioGroup,
  ContextMenuRadioItem,
  ContextMenuSeparator,
  ContextMenuSub,
  ContextMenuSubContent,
  ContextMenuSubTrigger,
  ContextMenuTrigger,
} from "@/components/ui/context-menu"
import { toast } from "@/components/ui/use-toast"
import { getDisplayName } from "@/lib/auth"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

interface CaseItemProps {
  caseData: CaseReadMinimal
  isSelected: boolean
  isChecked?: boolean
  onCheckChange?: (checked: boolean) => void
  onClick: () => void
  onDeleteRequest?: (caseData: CaseReadMinimal) => void
  tags?: CaseTagRead[]
  members?: WorkspaceMember[]
}

export function CaseItem({
  caseData,
  isSelected,
  isChecked = false,
  onCheckChange,
  onClick,
  onDeleteRequest,
  tags,
  members,
}: CaseItemProps) {
  const workspaceId = useWorkspaceId()
  const queryClient = useQueryClient()
  const priorityConfig = PRIORITIES[caseData.priority]
  const severityConfig = SEVERITIES[caseData.severity]

  // Track context menu open state for highlighting
  const [isContextMenuOpen, setIsContextMenuOpen] = useState(false)

  // Track optimistic tag state
  const [optimisticTags, setOptimisticTags] = useState<Set<string> | null>(null)

  // Reset optimistic state when server data changes
  useEffect(() => {
    setOptimisticTags(null)
  }, [caseData.tags])

  const handleCheckboxClick = (e: React.MouseEvent) => {
    e.stopPropagation()
    onCheckChange?.(!isChecked)
  }

  const handleCheckboxKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== " " && e.key !== "Enter") return
    e.preventDefault()
    e.stopPropagation()
    onCheckChange?.(!isChecked)
  }

  const handleCopyId = async () => {
    try {
      await navigator.clipboard.writeText(caseData.id)
      toast({
        title: "Case ID copied",
        description: (
          <div className="flex flex-col space-y-2">
            <span>
              Case ID copied for{" "}
              <b className="inline-block">{caseData.short_id}</b>
            </span>
            <span className="text-muted-foreground">ID: {caseData.id}</span>
          </div>
        ),
      })
    } catch (error) {
      console.error("Failed to copy to clipboard:", error)
      toast({
        title: "Failed to copy",
        description: "Could not copy case ID to clipboard",
        variant: "destructive",
      })
    }
  }

  const handleTagToggle = async (tagId: string, hasTag: boolean) => {
    // Optimistically update the UI immediately
    const currentTags = optimisticTags ?? new Set(caseData.tags?.map((t) => t.id) ?? [])
    const newTags = new Set(currentTags)
    if (hasTag) {
      newTags.delete(tagId)
    } else {
      newTags.add(tagId)
    }
    setOptimisticTags(newTags)

    try {
      if (hasTag) {
        await casesRemoveTag({
          caseId: caseData.id,
          tagIdentifier: tagId,
          workspaceId,
        })
        const tag = tags?.find((t) => t.id === tagId)
        toast({
          title: "Tag removed",
          description: `Successfully removed tag "${tag?.name}" from case`,
        })
      } else {
        await casesAddTag({
          caseId: caseData.id,
          workspaceId,
          requestBody: {
            tag_id: tagId,
          },
        })
        const tag = tags?.find((t) => t.id === tagId)
        toast({
          title: "Tag added",
          description: `Successfully added tag "${tag?.name}" to case`,
        })
      }
      // Invalidate to refetch - optimistic state will be reset by useEffect when new data arrives
      await queryClient.invalidateQueries({ queryKey: ["cases"] })
    } catch (error) {
      // Revert optimistic update on error
      setOptimisticTags(null)
      console.error("Failed to modify tag:", error)
      toast({
        title: "Error",
        description: `Failed to ${hasTag ? "remove" : "add"} tag ${hasTag ? "from" : "to"} case`,
        variant: "destructive",
      })
    }
  }

  const handleStatusChange = async (status: CaseStatus) => {
    if (status === caseData.status) return
    try {
      await casesUpdateCase({
        workspaceId,
        caseId: caseData.id,
        requestBody: { status },
      })
      toast({
        title: "Status updated",
        description: `Case status changed to ${STATUSES[status].label}`,
      })
      await queryClient.invalidateQueries({ queryKey: ["cases"] })
    } catch (error) {
      console.error("Failed to update status:", error)
      toast({
        title: "Error",
        description: "Failed to update case status",
        variant: "destructive",
      })
    }
  }

  const handlePriorityChange = async (priority: CasePriority) => {
    if (priority === caseData.priority) return
    try {
      await casesUpdateCase({
        workspaceId,
        caseId: caseData.id,
        requestBody: { priority },
      })
      toast({
        title: "Priority updated",
        description: `Case priority changed to ${PRIORITIES[priority].label}`,
      })
      await queryClient.invalidateQueries({ queryKey: ["cases"] })
    } catch (error) {
      console.error("Failed to update priority:", error)
      toast({
        title: "Error",
        description: "Failed to update case priority",
        variant: "destructive",
      })
    }
  }

  const handleSeverityChange = async (severity: CaseSeverity) => {
    if (severity === caseData.severity) return
    try {
      await casesUpdateCase({
        workspaceId,
        caseId: caseData.id,
        requestBody: { severity },
      })
      toast({
        title: "Severity updated",
        description: `Case severity changed to ${SEVERITIES[severity].label}`,
      })
      await queryClient.invalidateQueries({ queryKey: ["cases"] })
    } catch (error) {
      console.error("Failed to update severity:", error)
      toast({
        title: "Error",
        description: "Failed to update case severity",
        variant: "destructive",
      })
    }
  }

  const handleAssigneeChange = async (assigneeId: string) => {
    const newAssigneeId = assigneeId === UNASSIGNED ? null : assigneeId
    const currentAssigneeId = caseData.assignee?.id ?? null
    if (newAssigneeId === currentAssigneeId) return
    try {
      await casesUpdateCase({
        workspaceId,
        caseId: caseData.id,
        requestBody: { assignee_id: newAssigneeId },
      })
      const assigneeName = newAssigneeId
        ? members?.find((m) => m.user_id === newAssigneeId)?.email ?? "user"
        : "unassigned"
      toast({
        title: "Assignee updated",
        description: newAssigneeId
          ? `Case assigned to ${assigneeName}`
          : "Case unassigned",
      })
      await queryClient.invalidateQueries({ queryKey: ["cases"] })
    } catch (error) {
      console.error("Failed to update assignee:", error)
      toast({
        title: "Error",
        description: "Failed to update case assignee",
        variant: "destructive",
      })
    }
  }

  // Compute effective tag state (optimistic or actual)
  const effectiveTagIds = optimisticTags ?? new Set(caseData.tags?.map((t) => t.id) ?? [])

  return (
    <ContextMenu onOpenChange={setIsContextMenuOpen}>
      <ContextMenuTrigger asChild>
        <button
          type="button"
          onClick={onClick}
          className={cn(
            "group/item",
            // Use negative margins to extend hover to full width
            "-ml-[18px] flex w-[calc(100%+18px)] items-center gap-3 py-2 pl-3 pr-3 text-left transition-colors",
            "hover:bg-muted/50",
            isSelected && "bg-muted",
            isChecked && "bg-muted/30",
            // Highlight when context menu is open
            isContextMenuOpen && "bg-muted/70"
          )}
        >
          {/* Checkbox - flat design, hidden by default, shown on hover or when checked */}
          <div
            className="flex h-7 w-7 shrink-0 items-center justify-center"
            onClick={handleCheckboxClick}
          >
            <div
              className={cn(
                "flex size-4 shrink-0 items-center justify-center rounded-sm border transition-colors",
                // Hidden by default, visible on hover or when checked
                !isChecked && "opacity-0 group-hover/item:opacity-100",
                // Flat design - no shadows
                isChecked
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-muted-foreground/40 bg-transparent"
              )}
              role="checkbox"
              aria-checked={isChecked}
              aria-label={`Select case ${caseData.short_id}`}
              tabIndex={0}
              onKeyDown={handleCheckboxKeyDown}
            >
              {isChecked && <Check className="size-3" aria-hidden />}
            </div>
          </div>

          {/* Case ID + Summary + Badges */}
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <span className="shrink-0 text-xs font-medium text-muted-foreground">
              {caseData.short_id}
            </span>
            <span className="truncate text-xs">{caseData.summary}</span>
            {/* Badges - right next to summary */}
            {priorityConfig && (
              <CaseBadge
                value={caseData.priority}
                label={priorityConfig.label}
                icon={priorityConfig.icon}
                color={priorityConfig.color}
                className="h-5 shrink-0 px-1.5 py-0 text-[10px]"
              />
            )}
            {severityConfig && (
              <CaseBadge
                value={caseData.severity}
                label={severityConfig.label}
                icon={severityConfig.icon}
                color={severityConfig.color}
                className="h-5 shrink-0 px-1.5 py-0 text-[10px]"
              />
            )}
          </div>

          {/* Tags - right aligned */}
          {caseData.tags && caseData.tags.length > 0 && (
            <div className="flex shrink-0 items-center gap-1">
              {caseData.tags.slice(0, 3).map((tag) => (
                <span
                  key={tag.id}
                  className={cn(
                    "inline-flex h-5 items-center rounded-full px-2 text-[10px] font-medium",
                    !tag.color && "bg-muted text-muted-foreground"
                  )}
                  style={
                    tag.color
                      ? {
                          backgroundColor: `${tag.color}20`,
                          color: tag.color,
                        }
                      : undefined
                  }
                >
                  {tag.name}
                </span>
              ))}
              {caseData.tags.length > 3 && (
                <span className="text-[10px] text-muted-foreground">
                  +{caseData.tags.length - 3}
                </span>
              )}
            </div>
          )}

          {/* Timestamps */}
          <div className="flex shrink-0 items-center gap-2">
            <EventCreatedAt createdAt={caseData.created_at} />
            <EventUpdatedAt updatedAt={caseData.updated_at} />
          </div>
        </button>
      </ContextMenuTrigger>
      <ContextMenuContent className="w-48">
        <ContextMenuItem asChild className="text-xs">
          <Link
            href={`/workspaces/${workspaceId}/cases/${caseData.id}`}
            target="_blank"
            rel="noopener noreferrer"
          >
            <ExternalLink className="mr-2 size-3.5" />
            Open in new tab
          </Link>
        </ContextMenuItem>

        <ContextMenuItem className="text-xs" onClick={handleCopyId}>
          <Copy className="mr-2 size-3.5" />
          Copy case ID
        </ContextMenuItem>

        <ContextMenuSeparator />

        {/* Status submenu */}
        <ContextMenuSub>
          <ContextMenuSubTrigger className="text-xs">
            <SignalIcon className="mr-2 size-3.5" />
            Status
          </ContextMenuSubTrigger>
          <ContextMenuSubContent className="w-40">
            <ContextMenuRadioGroup value={caseData.status}>
              {Object.values(STATUSES).map((status) => {
                const StatusIcon = status.icon
                return (
                  <ContextMenuRadioItem
                    key={status.value}
                    value={status.value}
                    className="text-xs"
                    onClick={() => handleStatusChange(status.value)}
                  >
                    <StatusIcon className="mr-2 size-3.5" />
                    {status.label}
                  </ContextMenuRadioItem>
                )
              })}
            </ContextMenuRadioGroup>
          </ContextMenuSubContent>
        </ContextMenuSub>

        {/* Priority submenu */}
        <ContextMenuSub>
          <ContextMenuSubTrigger className="text-xs">
            <SignalHighIcon className="mr-2 size-3.5" />
            Priority
          </ContextMenuSubTrigger>
          <ContextMenuSubContent className="w-40">
            <ContextMenuRadioGroup value={caseData.priority}>
              {Object.values(PRIORITIES).map((priority) => {
                const PriorityIcon = priority.icon
                return (
                  <ContextMenuRadioItem
                    key={priority.value}
                    value={priority.value}
                    className="text-xs"
                    onClick={() => handlePriorityChange(priority.value)}
                  >
                    <PriorityIcon className="mr-2 size-3.5" />
                    {priority.label}
                  </ContextMenuRadioItem>
                )
              })}
            </ContextMenuRadioGroup>
          </ContextMenuSubContent>
        </ContextMenuSub>

        {/* Severity submenu */}
        <ContextMenuSub>
          <ContextMenuSubTrigger className="text-xs">
            <ShieldAlertIcon className="mr-2 size-3.5" />
            Severity
          </ContextMenuSubTrigger>
          <ContextMenuSubContent className="w-40">
            <ContextMenuRadioGroup value={caseData.severity}>
              {Object.values(SEVERITIES).map((severity) => {
                const SeverityIcon = severity.icon
                return (
                  <ContextMenuRadioItem
                    key={severity.value}
                    value={severity.value}
                    className="text-xs"
                    onClick={() => handleSeverityChange(severity.value)}
                  >
                    <SeverityIcon className="mr-2 size-3.5" />
                    {severity.label}
                  </ContextMenuRadioItem>
                )
              })}
            </ContextMenuRadioGroup>
          </ContextMenuSubContent>
        </ContextMenuSub>

        {/* Assignee submenu */}
        <ContextMenuSub>
          <ContextMenuSubTrigger className="text-xs">
            <UserIcon className="mr-2 size-3.5" />
            Assignee
          </ContextMenuSubTrigger>
          <ContextMenuSubContent className="w-48">
            <ContextMenuRadioGroup value={caseData.assignee?.id ?? UNASSIGNED}>
              <ContextMenuRadioItem
                value={UNASSIGNED}
                className="text-xs"
                onClick={() => handleAssigneeChange(UNASSIGNED)}
              >
                <UserIcon className="mr-2 size-3.5 text-muted-foreground" />
                Unassigned
              </ContextMenuRadioItem>
              {members?.map((member) => {
                const displayName = getDisplayName({
                  first_name: member.first_name,
                  last_name: member.last_name,
                  email: member.email,
                })
                const initials = member.first_name
                  ? member.first_name[0].toUpperCase()
                  : member.email[0].toUpperCase()
                return (
                  <ContextMenuRadioItem
                    key={member.user_id}
                    value={member.user_id}
                    className="text-xs"
                    onClick={() => handleAssigneeChange(member.user_id)}
                  >
                    <Avatar className="mr-2 size-4">
                      <AvatarFallback className="text-[8px] font-medium">
                        {initials}
                      </AvatarFallback>
                    </Avatar>
                    <span className="truncate">{displayName}</span>
                  </ContextMenuRadioItem>
                )
              })}
            </ContextMenuRadioGroup>
          </ContextMenuSubContent>
        </ContextMenuSub>

        {/* Tags submenu */}
        {tags && tags.length > 0 ? (
          <ContextMenuSub>
            <ContextMenuSubTrigger className="text-xs">
              <TagsIcon className="mr-2 size-3.5" />
              Tags
            </ContextMenuSubTrigger>
            <ContextMenuSubContent className="w-48">
              {tags.map((tag) => {
                const hasTag = effectiveTagIds.has(tag.id)
                return (
                  <ContextMenuCheckboxItem
                    key={tag.id}
                    className="text-xs"
                    checked={hasTag}
                    onClick={async (e) => {
                      e.preventDefault()
                      await handleTagToggle(tag.id, hasTag)
                    }}
                  >
                    <div
                      className={cn(
                        "mr-2 flex size-2 shrink-0 rounded-full",
                        !tag.color && "border border-muted-foreground/50 bg-muted"
                      )}
                      style={{
                        backgroundColor: tag.color || undefined,
                      }}
                    />
                    <span>{tag.name}</span>
                  </ContextMenuCheckboxItem>
                )
              })}
            </ContextMenuSubContent>
          </ContextMenuSub>
        ) : (
          <ContextMenuItem
            className="!bg-transparent text-xs !text-muted-foreground hover:cursor-not-allowed"
            disabled
          >
            <TagsIcon className="mr-2 size-3.5" />
            <span>No tags available</span>
          </ContextMenuItem>
        )}

        <ContextMenuSeparator />

        <ContextMenuItem
          className="text-xs text-rose-500 focus:text-rose-600"
          onClick={() => onDeleteRequest?.(caseData)}
        >
          <Trash2 className="mr-2 size-3.5" />
          Delete
        </ContextMenuItem>
      </ContextMenuContent>
    </ContextMenu>
  )
}
