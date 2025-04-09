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
      <SelectTrigger
        className={cn(
          "w-full focus:ring-0",
          inputVariants({ variant: "flat" })
        )}
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          <SelectLabel>Status</SelectLabel>
          {STATUSES.map((status) => (
            <SelectItem key={status.value} value={status.value}>
              <span className="flex items-center text-xs">
                {status.icon && (
                  <status.icon className="mr-2 size-4 text-muted-foreground" />
                )}
                {status.label}
              </span>
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
      <SelectTrigger
        className={cn(
          "w-full focus:ring-0",
          inputVariants({ variant: "flat" })
        )}
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent className="flex w-full">
        <SelectGroup>
          <SelectLabel>Priority</SelectLabel>
          {PRIORITIES.map(({ label, value, icon: Icon }) => (
            <SelectItem key={value} value={value} className="flex w-full">
              <CaseBadge
                label={label}
                icon={Icon}
                value={value}
                className="inline-flex w-full border-none"
              />
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
      <SelectTrigger
        className={cn(
          "w-full focus:ring-0",
          inputVariants({ variant: "flat" })
        )}
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          <SelectLabel>Severity</SelectLabel>
          {SEVERITIES.map(({ label, value, icon: Icon }) => (
            <SelectItem key={value} value={value} className="flex w-full">
              <CaseBadge
                label={label}
                value={value}
                icon={Icon}
                className="inline-flex w-full border-none"
              />
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  )
}
