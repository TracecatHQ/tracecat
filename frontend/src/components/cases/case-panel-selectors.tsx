"use client"

import { UserIcon } from "lucide-react"
import type {
  CasePriority,
  CaseSeverity,
  CaseStatus,
  UserRead,
  WorkspaceMember,
} from "@/client"
import {
  PRIORITIES,
  SEVERITIES,
  STATUSES,
} from "@/components/cases/case-categories"
import { CaseValueDisplay } from "@/components/cases/case-value-display"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import UserAvatar from "@/components/user-avatar"
import { User } from "@/lib/auth"
import { cn, linearStyles } from "@/lib/utils"

// Color mappings for Linear-style display
function getPriorityColor(priority: CasePriority): string {
  switch (priority) {
    case "high":
    case "critical":
      return "text-red-600"
    case "medium":
      return "text-orange-600"
    case "low":
      return "text-gray-600"
    default:
      return "text-muted-foreground"
  }
}

function getSeverityColor(severity: CaseSeverity): string {
  switch (severity) {
    case "high":
    case "critical":
    case "fatal":
      return "text-red-600"
    case "informational":
      return "text-blue-600"
    case "medium":
      return "text-orange-600"
    case "low":
      return "text-gray-600"
    case "other":
      return "text-gray-600"
    default:
      return "text-muted-foreground"
  }
}

function getStatusColor(status: CaseStatus): string {
  switch (status) {
    case "new":
      return "text-yellow-600"
    case "in_progress":
      return "text-blue-600"
    case "on_hold":
      return "text-orange-600"
    case "resolved":
      return "text-green-600"
    case "closed":
      return "text-violet-600"
    case "other":
      return "text-gray-600"
    default:
      return "text-muted-foreground"
  }
}

interface StatusSelectProps {
  status: CaseStatus
  onValueChange: (status: CaseStatus) => void
}

export function StatusSelect({ status, onValueChange }: StatusSelectProps) {
  const currentStatus = STATUSES[status]

  return (
    <Select value={status} onValueChange={onValueChange}>
      <SelectTrigger
        className={cn(linearStyles.trigger.base, linearStyles.trigger.hover)}
      >
        <SelectValue>
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">Status</span>
            <CaseValueDisplay
              icon={currentStatus.icon}
              label={currentStatus.label}
              color={getStatusColor(currentStatus.value)}
            />
          </div>
        </SelectValue>
      </SelectTrigger>
      <SelectContent>
        {Object.values(STATUSES).map((props) => (
          <SelectItem key={props.value} value={props.value}>
            <CaseValueDisplay
              icon={props.icon}
              label={props.label}
              color={getStatusColor(props.value)}
            />
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

interface PrioritySelectProps {
  priority: CasePriority
  onValueChange: (priority: CasePriority) => void
}

export function PrioritySelect({
  priority,
  onValueChange,
}: PrioritySelectProps) {
  const currentPriority = PRIORITIES[priority]

  return (
    <Select value={priority} onValueChange={onValueChange}>
      <SelectTrigger
        className={cn(linearStyles.trigger.base, linearStyles.trigger.hover)}
      >
        <SelectValue>
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">Priority</span>
            <CaseValueDisplay
              icon={currentPriority.icon}
              label={currentPriority.label}
              color={getPriorityColor(currentPriority.value)}
            />
          </div>
        </SelectValue>
      </SelectTrigger>
      <SelectContent>
        {Object.values(PRIORITIES).map((props) => (
          <SelectItem key={props.value} value={props.value}>
            <CaseValueDisplay
              icon={props.icon}
              label={props.label}
              color={getPriorityColor(props.value)}
            />
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

interface SeveritySelectProps {
  severity: CaseSeverity
  onValueChange: (severity: CaseSeverity) => void
}

export function SeveritySelect({
  severity,
  onValueChange,
}: SeveritySelectProps) {
  const currentSeverity = SEVERITIES[severity]

  return (
    <Select value={severity} onValueChange={onValueChange}>
      <SelectTrigger
        className={cn(linearStyles.trigger.base, linearStyles.trigger.hover)}
      >
        <SelectValue>
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">Severity</span>
            <CaseValueDisplay
              icon={currentSeverity.icon}
              label={currentSeverity.label}
              color={getSeverityColor(currentSeverity.value)}
            />
          </div>
        </SelectValue>
      </SelectTrigger>
      <SelectContent>
        {Object.values(SEVERITIES).map((props) => (
          <SelectItem key={props.value} value={props.value}>
            <CaseValueDisplay
              icon={props.icon}
              label={props.label}
              color={getSeverityColor(props.value)}
            />
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

export const UNASSIGNED = "__UNASSIGNED__" as const

interface AssigneeSelectProps {
  assignee?: UserRead | null
  workspaceMembers: WorkspaceMember[]
  onValueChange: (assignee?: UserRead | null) => void
}

export function AssigneeSelect({
  assignee,
  workspaceMembers,
  onValueChange,
}: AssigneeSelectProps) {
  return (
    <Select
      value={assignee?.id ?? UNASSIGNED}
      onValueChange={(value) => {
        if (value === UNASSIGNED) {
          onValueChange(null)
          return
        }
        const user = workspaceMembers.find((user) => user.user_id === value)
        if (user) {
          onValueChange({
            id: user.user_id,
            email: user.email,
            role: user.org_role,
            settings: {},
            first_name: user.first_name,
            last_name: user.last_name,
          })
        } else {
          onValueChange(null)
        }
      }}
    >
      <SelectTrigger
        className={cn(linearStyles.trigger.base, linearStyles.trigger.hover)}
      >
        <SelectValue>
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">Assignee</span>
            {assignee ? (
              <div className="flex items-center gap-1.5">
                <UserAvatar
                  alt={assignee.first_name || assignee.email}
                  user={
                    new User({
                      id: assignee.id,
                      email: assignee.email,
                      role: assignee.role,
                      first_name: assignee.first_name,
                      last_name: assignee.last_name,
                      settings: assignee.settings || {},
                    })
                  }
                  className="size-5 text-xs text-foreground"
                />
                <span className="text-xs font-medium">
                  {assignee.first_name || assignee.email.split("@")[0]}
                </span>
              </div>
            ) : (
              <NoAssignee className="text-xs" labelClassName="text-xs" />
            )}
          </div>
        </SelectValue>
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={UNASSIGNED}>
          <NoAssignee
            text="Unassigned"
            className="text-sm"
            labelClassName="text-sm"
          />
        </SelectItem>
        {workspaceMembers.length === 0 ? (
          <div className="flex items-center justify-center p-4 text-xs text-muted-foreground">
            No users available to assign
          </div>
        ) : (
          workspaceMembers.map((member) => {
            const user = new User({
              id: member.user_id,
              email: member.email,
              role: member.org_role,
              first_name: member.first_name,
              last_name: member.last_name,
              settings: {},
            })
            return (
              <SelectItem key={user.id} value={user.id}>
                <AssignedUser user={user} />
              </SelectItem>
            )
          })
        )}
      </SelectContent>
    </Select>
  )
}

export function NoAssignee({
  text,
  className,
  labelClassName,
}: {
  text?: string
  className?: string
  labelClassName?: string
}) {
  const baseClass = "flex items-center gap-1.5 text-xs leading-4"
  return (
    <div className={cn(baseClass, "text-muted-foreground", className)}>
      <div className="flex size-4 items-center justify-center rounded-full border border-dashed border-muted-foreground/50">
        <UserIcon className="size-3 text-muted-foreground" />
      </div>
      <span className={cn("text-xs text-muted-foreground", labelClassName)}>
        {text ?? "Unassigned"}
      </span>
    </div>
  )
}

export function AssignedUser({
  user,
  className,
  nameClassName,
}: {
  user: User
  className?: string
  nameClassName?: string
}) {
  const displayName = user.getDisplayName()
  return (
    <div
      className={cn(
        "flex min-w-0 items-center gap-1.5 text-xs leading-4",
        className
      )}
    >
      <UserAvatar
        alt={displayName}
        user={user}
        className="size-4 text-foreground"
        fallbackClassName="text-[10px]"
      />
      <span className="truncate text-xs" title={displayName}>
        {displayName}
      </span>
    </div>
  )
}
