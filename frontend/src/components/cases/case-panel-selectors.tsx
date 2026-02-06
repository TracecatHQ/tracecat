"use client"

import { CircleIcon, ListIcon, UserIcon } from "lucide-react"
import type {
  CaseDropdownDefinitionRead,
  CaseDropdownValueRead,
  CasePriority,
  CaseSeverity,
  CaseStatus,
  WorkspaceMember,
} from "@/client"
import {
  PRIORITIES,
  SEVERITIES,
  STATUSES,
} from "@/components/cases/case-categories"
import { CaseValueDisplay } from "@/components/cases/case-value-display"
import { DynamicLucideIcon } from "@/components/dynamic-lucide-icon"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import UserAvatar from "@/components/user-avatar"
import { getDisplayName } from "@/lib/auth"
import { cn, linearStyles } from "@/lib/utils"

/**
 * Minimal user info for display purposes (assignee selection).
 */
export interface AssigneeInfo {
  id: string
  email: string
  first_name?: string | null
  last_name?: string | null
}

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
  assignee?: AssigneeInfo | null
  workspaceMembers: WorkspaceMember[]
  onValueChange: (assignee?: AssigneeInfo | null) => void
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
                  email={assignee.email}
                  firstName={assignee.first_name}
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
          workspaceMembers.map((member) => (
            <SelectItem key={member.user_id} value={member.user_id}>
              <AssignedUser
                email={member.email}
                firstName={member.first_name}
                lastName={member.last_name}
              />
            </SelectItem>
          ))
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
  email,
  firstName,
  lastName,
  className,
}: {
  email: string
  firstName?: string | null
  lastName?: string | null
  className?: string
}) {
  const displayName = getDisplayName({
    email,
    first_name: firstName,
    last_name: lastName,
  })
  return (
    <div
      className={cn(
        "flex min-w-0 items-center gap-1.5 text-xs leading-4",
        className
      )}
    >
      <UserAvatar
        alt={displayName}
        email={email}
        firstName={firstName}
        className="size-4 text-foreground"
        fallbackClassName="text-[10px]"
      />
      <span className="truncate text-xs" title={displayName}>
        {displayName}
      </span>
    </div>
  )
}

// --- Case Dropdown Select ---

const NONE_VALUE = "__NONE__"

interface CaseDropdownSelectProps {
  definition: CaseDropdownDefinitionRead
  currentValue: CaseDropdownValueRead | undefined
  onValueChange: (optionId: string | null) => void
}

export function CaseDropdownSelect({
  definition,
  currentValue,
  onValueChange,
}: CaseDropdownSelectProps) {
  const currentOptionId = currentValue?.option_id ?? NONE_VALUE
  const currentOption = definition.options?.find(
    (o) => o.id === currentValue?.option_id
  )
  const currentOptionStyle = currentOption?.color
    ? ({ color: currentOption.color } as React.CSSProperties)
    : undefined

  return (
    <Select
      value={currentOptionId}
      onValueChange={(val) => onValueChange(val === NONE_VALUE ? null : val)}
    >
      <SelectTrigger
        className={cn(linearStyles.trigger.base, linearStyles.trigger.hover)}
      >
        <SelectValue>
          <div className="flex items-center gap-2">
            {definition.icon_name ? (
              <DynamicLucideIcon
                name={definition.icon_name}
                className="size-3.5 text-muted-foreground"
                fallback={<ListIcon className="size-3.5 text-muted-foreground" />}
              />
            ) : (
              <ListIcon className="size-3.5 text-muted-foreground" />
            )}
            <span className="text-xs text-muted-foreground">
              {definition.name}
            </span>
            {currentOption ? (
              <div className="flex items-center gap-1.5 text-xs">
                {currentOption.icon_name ? (
                  <DynamicLucideIcon
                    name={currentOption.icon_name}
                    className="size-3.5"
                    style={currentOptionStyle}
                    fallback={
                      <CircleIcon
                        className="size-3.5 text-muted-foreground"
                        style={currentOptionStyle}
                      />
                    }
                  />
                ) : currentOption.color ? (
                  <CircleIcon className="size-3.5" style={currentOptionStyle} />
                ) : (
                  <CircleIcon className="size-3.5 text-muted-foreground" />
                )}
                <span style={currentOptionStyle}>{currentOption.label}</span>
              </div>
            ) : (
              <span className="text-xs text-muted-foreground">None</span>
            )}
          </div>
        </SelectValue>
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={NONE_VALUE}>
          <span className="text-muted-foreground">None</span>
        </SelectItem>
        {definition.options?.map((opt) => {
          const optionStyle = opt.color
            ? ({ color: opt.color } as React.CSSProperties)
            : undefined
          return (
            <SelectItem key={opt.id} value={opt.id}>
              <div className="flex items-center gap-1.5">
                {opt.icon_name ? (
                  <DynamicLucideIcon
                    name={opt.icon_name}
                    className="size-3.5"
                    style={optionStyle}
                    fallback={
                      <CircleIcon
                        className="size-3.5 text-muted-foreground"
                        style={optionStyle}
                      />
                    }
                  />
                ) : opt.color ? (
                  <CircleIcon className="size-3.5" style={optionStyle} />
                ) : null}
                <span style={optionStyle}>{opt.label}</span>
              </div>
            </SelectItem>
          )
        })}
      </SelectContent>
    </Select>
  )
}
