"use client"

import { Cross2Icon } from "@radix-ui/react-icons"
import type { CasePriority, CaseSeverity, CaseStatus } from "@/client"
import {
  PRIORITIES,
  SEVERITIES,
  STATUSES,
} from "@/components/cases/case-categories"
import { UNASSIGNED } from "@/components/cases/case-panel-selectors"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useWorkspaceMembers } from "@/hooks/use-workspace"
import { getDisplayName } from "@/lib/auth"

interface CaseTableFiltersProps {
  workspaceId: string
  searchTerm: string
  onSearchChange: (value: string) => void
  statusFilter: CaseStatus | null
  onStatusChange: (value: CaseStatus | null) => void
  priorityFilter: CasePriority | null
  onPriorityChange: (value: CasePriority | null) => void
  severityFilter: CaseSeverity | null
  onSeverityChange: (value: CaseSeverity | null) => void
  assigneeFilter: string | null
  onAssigneeChange: (value: string | null) => void
}

export function CaseTableFilters({
  workspaceId,
  searchTerm,
  onSearchChange,
  statusFilter,
  onStatusChange,
  priorityFilter,
  onPriorityChange,
  severityFilter,
  onSeverityChange,
  assigneeFilter,
  onAssigneeChange,
}: CaseTableFiltersProps) {
  const { members } = useWorkspaceMembers(workspaceId)

  const hasFilters =
    statusFilter ||
    priorityFilter ||
    severityFilter ||
    assigneeFilter ||
    searchTerm

  const handleReset = () => {
    onSearchChange("")
    onStatusChange(null)
    onPriorityChange(null)
    onSeverityChange(null)
    onAssigneeChange(null)
  }

  return (
    <div className="flex items-center gap-2">
      <Input
        placeholder="Filter cases..."
        value={searchTerm}
        onChange={(e) => onSearchChange(e.target.value)}
        className="h-8 w-[250px] text-xs"
      />

      <Select
        value={statusFilter || undefined}
        onValueChange={(value) => onStatusChange(value as CaseStatus)}
      >
        <SelectTrigger className="h-8 w-[140px] text-xs">
          <SelectValue placeholder="Status" />
        </SelectTrigger>
        <SelectContent>
          {Object.values(STATUSES).map((status) => (
            <SelectItem key={status.value} value={status.value}>
              <div className="flex items-center gap-2">
                <status.icon className={`size-3 ${status.className}`} />
                <span>{status.label}</span>
              </div>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={priorityFilter || undefined}
        onValueChange={(value) => onPriorityChange(value as CasePriority)}
      >
        <SelectTrigger className="h-8 w-[140px] text-xs">
          <SelectValue placeholder="Priority" />
        </SelectTrigger>
        <SelectContent>
          {Object.values(PRIORITIES).map((priority) => (
            <SelectItem key={priority.value} value={priority.value}>
              <div className="flex items-center gap-2">
                <priority.icon className={`size-3 ${priority.className}`} />
                <span>{priority.label}</span>
              </div>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={severityFilter || undefined}
        onValueChange={(value) => onSeverityChange(value as CaseSeverity)}
      >
        <SelectTrigger className="h-8 w-[140px] text-xs">
          <SelectValue placeholder="Severity" />
        </SelectTrigger>
        <SelectContent>
          {Object.values(SEVERITIES).map((severity) => (
            <SelectItem key={severity.value} value={severity.value}>
              <div className="flex items-center gap-2">
                <severity.icon className={`size-3 ${severity.className}`} />
                <span>{severity.label}</span>
              </div>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={assigneeFilter || undefined}
        onValueChange={(value) => onAssigneeChange(value || null)}
      >
        <SelectTrigger className="h-8 w-[160px] text-xs">
          <SelectValue placeholder="Assignee" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={UNASSIGNED}>Not assigned</SelectItem>
          {members?.map((member) => {
            const displayName = getDisplayName({
              first_name: member.first_name,
              last_name: member.last_name,
              email: member.email,
            })
            return (
              <SelectItem key={member.user_id} value={member.user_id}>
                {displayName}
              </SelectItem>
            )
          })}
        </SelectContent>
      </Select>

      {hasFilters && (
        <Button
          variant="ghost"
          onClick={handleReset}
          className="h-8 px-2 text-xs text-foreground/80 lg:px-3"
        >
          Reset
          <Cross2Icon className="ml-2 size-4" />
        </Button>
      )}
    </div>
  )
}
