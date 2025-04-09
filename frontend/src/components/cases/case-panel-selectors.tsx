"use client"

import React from "react"
import { CasePriority, CaseSeverity, CaseStatus } from "@/client"

import { cn } from "@/lib/utils"
import { inputVariants } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
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
        <SelectGroup>
          <SelectLabel>Status</SelectLabel>
          {STATUSES.map((props) => (
            <SelectItem
              key={props.value}
              value={props.value}
              className="flex w-full"
            >
              <CaseBadge {...props} />
            </SelectItem>
          ))}
        </SelectGroup>
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
        <SelectGroup>
          <SelectLabel>Priority</SelectLabel>
          {PRIORITIES.map((props) => (
            <SelectItem
              key={props.value}
              value={props.value}
              className="flex w-full"
            >
              <CaseBadge {...props} />
            </SelectItem>
          ))}
        </SelectGroup>
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
        <SelectGroup>
          <SelectLabel>Severity</SelectLabel>
          {SEVERITIES.map((props) => (
            <SelectItem
              key={props.value}
              value={props.value}
              className="flex w-full"
            >
              <CaseBadge {...props} />
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  )
}
