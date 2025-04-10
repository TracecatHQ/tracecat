"use client"

import React from "react"
import { CasePriority, CaseSeverity, CaseStatus } from "@/client"

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
        {STATUSES.map((props) => (
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
        {PRIORITIES.map((props) => (
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
        {SEVERITIES.map((props) => (
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
