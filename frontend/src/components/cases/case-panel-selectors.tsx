"use client"

import React from "react"
import {
  CasePriority,
  CaseSeverity,
  CaseStatus,
  UserRead,
  WorkspaceMember,
} from "@/client"
import { UserIcon } from "lucide-react"

import { User } from "@/lib/auth"
import { cn } from "@/lib/utils"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { CaseBadge } from "@/components/cases/case-badge"
import {
  PRIORITIES,
  SEVERITIES,
  STATUSES,
} from "@/components/cases/case-categories"
import UserAvatar from "@/components/user-avatar"

interface StatusSelectProps {
  status: CaseStatus
  onValueChange: (status: CaseStatus) => void
}

export function StatusSelect({ status, onValueChange }: StatusSelectProps) {
  return (
    <Select defaultValue={status} onValueChange={onValueChange}>
      <SelectTrigger variant="flat">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {Object.values(STATUSES).map((props) => (
          <SelectItem
            key={props.value}
            value={props.value}
            className="flex w-full"
          >
            <CaseBadge {...props} />
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
  return (
    <Select defaultValue={priority} onValueChange={onValueChange}>
      <SelectTrigger variant="flat">
        <SelectValue />
      </SelectTrigger>
      <SelectContent className="flex w-full">
        {Object.values(PRIORITIES).map((props) => (
          <SelectItem
            key={props.value}
            value={props.value}
            className="flex w-full"
          >
            <CaseBadge {...props} />
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
  return (
    <Select defaultValue={severity} onValueChange={onValueChange}>
      <SelectTrigger variant="flat">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {Object.values(SEVERITIES).map((props) => (
          <SelectItem
            key={props.value}
            value={props.value}
            className="flex w-full"
          >
            <CaseBadge {...props} />
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
      defaultValue={assignee?.id ?? UNASSIGNED}
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
      <SelectTrigger variant="flat">
        <SelectValue placeholder={<NoAssignee />} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={UNASSIGNED}>
          <NoAssignee text="Unassigned" />
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
}: {
  text?: string
  className?: string
}) {
  return (
    <div
      className={cn("flex items-center gap-2 text-muted-foreground", className)}
    >
      <div className="flex size-6 items-center justify-center rounded-full border border-dashed border-muted-foreground/70 bg-muted">
        <UserIcon className="size-4 text-muted-foreground" />
      </div>
      <span>{text ?? "Unassigned"}</span>
    </div>
  )
}

export function AssignedUser({
  user,
  className,
}: {
  user: User
  className?: string
}) {
  const displayName = user.getDisplayName()
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <UserAvatar
        alt={displayName}
        user={user}
        className="size-6 text-xs text-foreground"
      />
      <span>{displayName}</span>
    </div>
  )
}
