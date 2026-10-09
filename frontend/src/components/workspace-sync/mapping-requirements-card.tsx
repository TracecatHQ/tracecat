"use client"

import { useMemo } from "react"
import type {
  CatalogMappingCandidate,
  CatalogMappingRequirement,
  CatalogMappingSelection,
  McpIntegrationMappingRequirement,
  McpIntegrationMappingSelection,
  SecretStoreMappingRequirement,
  SecretStoreMappingSelection,
} from "@/client"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"

/**
 * One unresolved source reference rendered as a mapping row:
 * an identity header, a target picker, and the list of affected documents.
 */
interface MappingRequirementItem {
  /** Source-side identifier; also the selections map key. */
  key: string
  title: string
  subtitle: string
  ariaLabel: string
  candidates: { value: string; label: string }[]
  /** Human-readable list of preset versions and workflow actions rewritten by this choice. */
  affects: string
  /** An optional choice: no selection is a valid answer, not a blocker. */
  optional?: boolean
}

/** Secret store choice that imports a store name's secrets without a store. */
export const LEAVE_UNLINKED = "__leave_unlinked__"

/**
 * Pull-time mapping requirements (model catalogs, MCP integrations) as rows:
 * the source reference, its status, a target picker, and what it affects.
 */
function MappingRequirementRows({
  placeholder,
  items,
  selections,
  onChange,
  disabled,
}: {
  placeholder: string
  items: MappingRequirementItem[]
  selections: Record<string, string>
  onChange: (sourceId: string, targetId: string) => void
  disabled: boolean
}) {
  return (
    <div className="divide-y">
      {items.map((item) => {
        const isMatched = Boolean(selections[item.key])
        let status = isMatched ? "Matched" : "Needs a match"
        if (item.optional) {
          status =
            isMatched && selections[item.key] !== LEAVE_UNLINKED
              ? "Linked"
              : "Unlinked"
        }
        return (
          <div key={item.key} className="space-y-2 py-3.5">
            <div className="flex min-w-0 items-baseline gap-2">
              <span className="truncate text-sm font-medium">{item.title}</span>
              <span className="truncate text-xs text-muted-foreground">
                {item.subtitle}
              </span>
              <span
                className={cn(
                  "ml-auto shrink-0 text-[11px]",
                  isMatched || item.optional
                    ? "text-muted-foreground"
                    : "text-amber-700 dark:text-amber-500"
                )}
              >
                {status}
              </span>
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
              Used by {item.affects}
            </p>
          </div>
        )
      })}
    </div>
  )
}

/**
 * Join a requirement's affected preset versions and workflow actions into the
 * "Used by ..." summary line.
 */
function mappingAffectsSummary(requirement: {
  affected_presets: { preset_name: string; version: number | null }[]
  affected_workflows: { workflow_title: string; action_ref: string }[]
}): string {
  return [
    ...requirement.affected_presets.map((preset) =>
      preset.version === null
        ? `${preset.preset_name} head`
        : `${preset.preset_name} version ${preset.version}`
    ),
    ...requirement.affected_workflows.map(
      (workflow) => `${workflow.workflow_title} action ${workflow.action_ref}`
    ),
  ].join(", ")
}

/**
 * Inline resolution UI for source models with more than one safe target match.
 */
export function CatalogMappingRequirements({
  requirements,
  selections,
  onChange,
  disabled,
}: {
  requirements: CatalogMappingRequirement[]
  selections: Record<string, string>
  onChange: (sourceCatalogId: string, targetCatalogId: string) => void
  disabled: boolean
}) {
  const items = useMemo(
    () =>
      requirements.map((requirement): MappingRequirementItem => {
        const baseLabels = requirement.candidates.map(
          catalogMappingCandidateBaseLabel
        )
        const baseLabelCounts = new Map<string, number>()
        for (const baseLabel of baseLabels) {
          baseLabelCounts.set(
            baseLabel,
            (baseLabelCounts.get(baseLabel) ?? 0) + 1
          )
        }
        // Only disambiguate with a catalog id fragment when two candidates
        // would otherwise render the same label.
        const candidates = requirement.candidates.map((candidate, index) => {
          const baseLabel = baseLabels[index]
          const isDuplicate = (baseLabelCounts.get(baseLabel) ?? 0) > 1
          return {
            value: candidate.catalog_id,
            label: isDuplicate
              ? `${baseLabel} · ${candidate.catalog_id.slice(0, 8)}`
              : baseLabel,
          }
        })
        return {
          key: requirement.source_catalog_id,
          title: requirement.model_name,
          subtitle: `Model · ${requirement.model_provider}`,
          ariaLabel: `Target model for ${requirement.model_name}`,
          candidates,
          affects: mappingAffectsSummary(requirement),
        }
      }),
    [requirements]
  )

  return (
    <MappingRequirementRows
      placeholder="Choose a model"
      items={items}
      selections={selections}
      onChange={onChange}
      disabled={disabled}
    />
  )
}

function catalogMappingCandidateBaseLabel(
  candidate: CatalogMappingCandidate
): string {
  const details = [candidate.provider_name]
  if (
    candidate.model_display_name &&
    candidate.model_display_name !== candidate.model_name
  ) {
    details.push(candidate.model_display_name)
  }
  if (candidate.endpoint_hostname) {
    details.push(candidate.endpoint_hostname)
  }
  return details.join(" · ")
}

/**
 * Inline resolution UI for imported MCP integration references that could not
 * be resolved against a workspace-local integration.
 */
export function McpIntegrationMappingRequirements({
  requirements,
  selections,
  onChange,
  disabled,
}: {
  requirements: McpIntegrationMappingRequirement[]
  selections: Record<string, string>
  onChange: (
    sourceMcpIntegrationId: string,
    targetMcpIntegrationId: string
  ) => void
  disabled: boolean
}) {
  const items = useMemo(
    () =>
      requirements.map((requirement): MappingRequirementItem => {
        const nameCounts = new Map<string, number>()
        for (const candidate of requirement.candidates) {
          nameCounts.set(
            candidate.name,
            (nameCounts.get(candidate.name) ?? 0) + 1
          )
        }
        // Slugs are workspace-unique, so only append one when two candidates
        // would otherwise render the same name.
        const candidates = requirement.candidates.map((candidate) => {
          const isDuplicate = (nameCounts.get(candidate.name) ?? 0) > 1
          const name = isDuplicate
            ? `${candidate.name} (${candidate.slug})`
            : candidate.name
          return {
            value: candidate.mcp_integration_id,
            label: `${name} (${candidate.server_type} · ${candidate.auth_type})`,
          }
        })
        return {
          key: requirement.source_mcp_integration_id,
          title:
            requirement.name ??
            requirement.slug ??
            requirement.source_mcp_integration_id,
          subtitle: "MCP integration",
          ariaLabel: `Target MCP integration for ${
            requirement.slug ?? requirement.source_mcp_integration_id
          }`,
          candidates,
          affects: mappingAffectsSummary(requirement),
        }
      }),
    [requirements]
  )

  return (
    <MappingRequirementRows
      placeholder="Choose an MCP integration"
      items={items}
      selections={selections}
      onChange={onChange}
      disabled={disabled}
    />
  )
}

/**
 * Rows for AWS-backed secrets whose store name has no authorized match here.
 * Each is optional: unchosen names import their secrets without a store.
 */
export function SecretStoreMappingRequirements({
  requirements,
  selections,
  onChange,
  disabled,
}: {
  requirements: SecretStoreMappingRequirement[]
  selections: Record<string, string>
  onChange: (sourceStore: string, targetStoreId: string) => void
  disabled: boolean
}) {
  const items = useMemo(
    () =>
      requirements.map(
        (requirement): MappingRequirementItem => ({
          key: requirement.source_store,
          title: requirement.source_store,
          subtitle: "Secret store",
          ariaLabel: `Target store for ${requirement.source_store}`,
          candidates: [
            ...requirement.candidates.map((candidate) => ({
              value: candidate.store_id,
              label: candidate.region
                ? `${candidate.name} (${candidate.region})`
                : candidate.name,
            })),
            { value: LEAVE_UNLINKED, label: "Leave unlinked" },
          ],
          affects: requirement.affected_secrets
            .map((secret) => secret.secret_name)
            .join(", "),
          optional: true,
        })
      ),
    [requirements]
  )

  return (
    <MappingRequirementRows
      placeholder="Leave unlinked"
      items={items}
      selections={selections}
      onChange={onChange}
      disabled={disabled}
    />
  )
}

/**
 * Sorted secret store selections for a pull request body; "Leave unlinked"
 * is sent as a null target.
 */
export function secretStoreMappingSelections(
  mappings: Record<string, string>
): SecretStoreMappingSelection[] {
  return Object.entries(mappings)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([sourceStore, targetStoreId]) => ({
      source_store: sourceStore,
      target_store_id: targetStoreId === LEAVE_UNLINKED ? null : targetStoreId,
    }))
}

/**
 * Sorted catalog mapping selections for a pull request body.
 */
export function catalogMappingSelections(
  mappings: Record<string, string>
): CatalogMappingSelection[] {
  return Object.entries(mappings)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([sourceCatalogId, targetCatalogId]) => ({
      source_catalog_id: sourceCatalogId,
      target_catalog_id: targetCatalogId,
    }))
}

/**
 * Sorted MCP integration mapping selections for a pull request body.
 */
export function mcpIntegrationMappingSelections(
  mappings: Record<string, string>
): McpIntegrationMappingSelection[] {
  return Object.entries(mappings)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([sourceMcpIntegrationId, targetMcpIntegrationId]) => ({
      source_mcp_integration_id: sourceMcpIntegrationId,
      target_mcp_integration_id: targetMcpIntegrationId,
    }))
}
