"use client"

import { Check } from "lucide-react"
import type { CaseReadMinimal } from "@/client"
import { CaseBadge } from "@/components/cases/case-badge"
import { PRIORITIES, SEVERITIES } from "@/components/cases/case-categories"
import {
  EventCreatedAt,
  EventUpdatedAt,
} from "@/components/cases/cases-feed-event"
import { cn } from "@/lib/utils"

interface CaseItemProps {
  caseData: CaseReadMinimal
  isSelected: boolean
  isChecked?: boolean
  onCheckChange?: (checked: boolean) => void
  onClick: () => void
}

export function CaseItem({
  caseData,
  isSelected,
  isChecked = false,
  onCheckChange,
  onClick,
}: CaseItemProps) {
  const priorityConfig = PRIORITIES[caseData.priority]
  const severityConfig = SEVERITIES[caseData.severity]

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

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "group/item",
        // Use negative margins to extend hover to full width
        "-ml-[18px] flex w-[calc(100%+18px)] items-center gap-3 py-2 pl-3 pr-3 text-left transition-colors",
        "hover:bg-muted/50",
        isSelected && "bg-muted",
        isChecked && "bg-muted/30"
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

      {/* Timestamps */}
      <div className="flex shrink-0 items-center gap-2">
        <EventCreatedAt createdAt={caseData.created_at} />
        <EventUpdatedAt updatedAt={caseData.updated_at} />
      </div>
    </button>
  )
}
