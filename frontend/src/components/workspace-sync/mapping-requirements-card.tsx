"use client"

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

/**
 * One unresolved source reference rendered as a row in the mapping card:
 * an identity header, a target picker, and the list of affected documents.
 */
export interface MappingRequirementItem {
  /** Source-side identifier; also the selections map key. */
  key: string
  title: string
  subtitle: string
  ariaLabel: string
  candidates: { value: string; label: string }[]
  /** Human-readable list of preset versions and workflow actions rewritten by this choice. */
  affects: string
}

/**
 * Shared shell for pull-time mapping requirements (model catalogs, MCP
 * integrations): a heading, one picker row per unresolved source reference,
 * and a re-preview reminder once every reference has a selection.
 */
export function MappingRequirementsCard({
  heading,
  description,
  placeholder,
  items,
  selections,
  onChange,
  mappingsMatchPreview,
  disabled,
}: {
  heading: string
  description: string
  placeholder: string
  items: MappingRequirementItem[]
  selections: Record<string, string>
  onChange: (sourceId: string, targetId: string) => void
  mappingsMatchPreview: boolean
  disabled: boolean
}) {
  const allSelected = items.every((item) => selections[item.key])

  return (
    <div className="space-y-3 rounded-md border border-amber-200 bg-amber-50/40 p-3">
      <div className="space-y-1">
        <h6 className="text-sm font-medium">{heading}</h6>
        <p className="text-xs text-muted-foreground">{description}</p>
      </div>

      <div className="space-y-3">
        {items.map((item) => (
          <div
            key={item.key}
            className="space-y-2 border-t border-amber-200 pt-3 first:border-0 first:pt-0"
          >
            <div className="space-y-0.5">
              <div className="text-sm font-medium">{item.title}</div>
              <div className="text-xs text-muted-foreground">
                {item.subtitle}
              </div>
            </div>

            <Select
              value={selections[item.key] ?? ""}
              onValueChange={(targetId) => onChange(item.key, targetId)}
              disabled={disabled}
            >
              <SelectTrigger aria-label={item.ariaLabel}>
                <SelectValue placeholder={placeholder} />
              </SelectTrigger>
              <SelectContent>
                {item.candidates.map((candidate) => (
                  <SelectItem key={candidate.value} value={candidate.value}>
                    {candidate.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <p className="text-[11px] text-muted-foreground">
              Affects {item.affects}
            </p>
          </div>
        ))}
      </div>

      {allSelected && !mappingsMatchPreview && (
        <p className="text-xs font-medium text-amber-700">
          Preview changes again to validate these choices before applying.
        </p>
      )}
    </div>
  )
}

/**
 * Join a requirement's affected preset versions and workflow actions into the
 * "Affects ..." summary line.
 */
export function mappingAffectsSummary(requirement: {
  affected_presets: { preset_name: string; version: number }[]
  affected_workflows: { workflow_title: string; action_ref: string }[]
}): string {
  return [
    ...requirement.affected_presets.map(
      (preset) => `${preset.preset_name} version ${preset.version}`
    ),
    ...requirement.affected_workflows.map(
      (workflow) => `${workflow.workflow_title} action ${workflow.action_ref}`
    ),
  ].join(", ")
}
