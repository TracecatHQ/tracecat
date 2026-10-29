"use client"

import type {
  RolloutChange,
  RolloutConflict,
  RolloutDeleteWarning,
  RolloutDroppedSkill,
  RolloutPreview,
  RolloutReference,
  RolloutRegistryPlan,
} from "@/client"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { referenceKindLabel, resourceTypeLabel } from "../managed-package-utils"

function DetailSection({
  title,
  count,
  children,
}: {
  title: string
  count: number
  children: React.ReactNode
}) {
  return (
    <div className="space-y-2">
      <h4 className="text-sm font-medium">
        {title} <span className="text-muted-foreground">({count})</span>
      </h4>
      {count === 0 ? (
        <p className="text-xs text-muted-foreground">None</p>
      ) : (
        children
      )}
    </div>
  )
}

function ChangeList({ changes }: { changes: RolloutChange[] }) {
  return (
    <ul className="divide-y rounded-md border text-xs">
      {changes.map((change) => (
        <li
          key={`${change.resource_type}:${change.source_id}`}
          className="flex items-center gap-2 px-3 py-2"
        >
          <Badge variant="outline" className="w-16 justify-center font-normal">
            {change.change}
          </Badge>
          <span className="text-muted-foreground">
            {resourceTypeLabel(change.resource_type)}
          </span>
          <span className="truncate font-medium">{change.name}</span>
        </li>
      ))}
    </ul>
  )
}

function ConflictList({ conflicts }: { conflicts: RolloutConflict[] }) {
  return (
    <div className="space-y-2">
      <Alert variant="warning">
        <AlertTitle>Name conflicts block this rollout</AlertTitle>
        <AlertDescription className="text-xs">
          The customer must rename or delete this resource; it can&apos;t be
          resolved from here.
        </AlertDescription>
      </Alert>
      <ul className="divide-y rounded-md border text-xs">
        {conflicts.map((conflict) => (
          <li
            key={`${conflict.resource_type}:${conflict.source_id}`}
            className="space-y-1 px-3 py-2"
          >
            <div>
              <span className="text-muted-foreground">
                {resourceTypeLabel(conflict.resource_type)}
              </span>{" "}
              <span className="font-medium">{conflict.name}</span>
            </div>
            <div className="text-muted-foreground">
              Incoming resource clashes with existing customer resource{" "}
              <span className="font-mono">{conflict.existing_local_id}</span>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}

function ReferenceList({ references }: { references: RolloutReference[] }) {
  return (
    <ul className="divide-y rounded-md border text-xs">
      {references.map((reference) => (
        <li
          key={`${reference.kind}:${reference.source_key}`}
          className="flex items-center gap-2 px-3 py-2"
        >
          <span className="text-muted-foreground">
            {referenceKindLabel(reference.kind)}
          </span>
          <span className="font-medium">{reference.label}</span>
          <span className="ml-auto truncate font-mono text-muted-foreground">
            {reference.source_key}
          </span>
        </li>
      ))}
    </ul>
  )
}

/** Resources a rollout would delete, with what customer data they touch. */
export function DeleteWarningList({
  warnings,
}: {
  warnings: RolloutDeleteWarning[]
}) {
  return (
    <ul className="divide-y rounded-md border text-xs">
      {warnings.map((warning) => {
        const facts: string[] = []
        if (warning.table_rows != null) {
          facts.push(`${warning.table_rows} table rows`)
        }
        if (warning.cases_using != null) {
          facts.push(`${warning.cases_using} cases affected`)
        }
        if (warning.has_customer_values) {
          facts.push("has customer-set secret values")
        }
        const referencedBy = warning.referenced_by ?? []
        return (
          <li
            key={`${warning.resource_type}:${warning.source_id}`}
            className="space-y-1 px-3 py-2"
          >
            <div>
              <span className="text-muted-foreground">
                {resourceTypeLabel(warning.resource_type)}
              </span>{" "}
              <span className="font-medium">{warning.name}</span>
            </div>
            {facts.length > 0 && (
              <div className="text-destructive">{facts.join(", ")}</div>
            )}
            {referencedBy.length > 0 && (
              <div className="text-muted-foreground">
                Referenced by: {referencedBy.join(", ")}
              </div>
            )}
          </li>
        )
      })}
    </ul>
  )
}

function RegistryList({ plans }: { plans: RolloutRegistryPlan[] }) {
  return (
    <ul className="divide-y rounded-md border text-xs">
      {plans.map((plan) => (
        <li
          key={`${plan.origin}@${plan.version}`}
          className="flex items-start gap-2 px-3 py-2"
        >
          <Badge variant="outline" className="w-16 justify-center font-normal">
            {plan.action}
          </Badge>
          <div className="min-w-0 space-y-1">
            <div className="break-all font-mono">
              {plan.origin}@{plan.version}
            </div>
            {plan.reason && (
              <div className="text-muted-foreground">{plan.reason}</div>
            )}
          </div>
        </li>
      ))}
    </ul>
  )
}

function DroppedSkillList({ skills }: { skills: RolloutDroppedSkill[] }) {
  return (
    <ul className="divide-y rounded-md border text-xs">
      {skills.map((skill) => (
        <li key={`${skill.preset}:${skill.skill}`} className="px-3 py-2">
          Customer skill <span className="font-medium">{skill.skill}</span> is
          removed from preset{" "}
          <span className="font-medium">{skill.preset}</span>
        </li>
      ))}
    </ul>
  )
}

/** Every part of a rollout preview, one section per kind of finding. */
export function RolloutPreviewDetails({
  preview,
}: {
  preview: RolloutPreview
}) {
  const changes = preview.changes ?? []
  const conflicts = preview.conflicts ?? []
  const references = preview.references ?? []
  const deleteWarnings = preview.delete_warnings ?? []
  const registry = preview.registry ?? []
  const droppedSkills = preview.dropped_customer_skills ?? []
  const diagnostics = preview.diagnostics ?? []

  return (
    <div className="space-y-6">
      {conflicts.length > 0 && (
        <DetailSection title="Conflicts" count={conflicts.length}>
          <ConflictList conflicts={conflicts} />
        </DetailSection>
      )}
      <DetailSection title="Changes" count={changes.length}>
        <ChangeList changes={changes} />
      </DetailSection>
      <DetailSection title="Unresolved references" count={references.length}>
        <ReferenceList references={references} />
      </DetailSection>
      <DetailSection title="Delete warnings" count={deleteWarnings.length}>
        <DeleteWarningList warnings={deleteWarnings} />
      </DetailSection>
      <DetailSection title="Registry plan" count={registry.length}>
        <RegistryList plans={registry} />
      </DetailSection>
      <DetailSection
        title="Dropped customer skills"
        count={droppedSkills.length}
      >
        <DroppedSkillList skills={droppedSkills} />
      </DetailSection>
      {diagnostics.length > 0 && (
        <DetailSection title="Diagnostics" count={diagnostics.length}>
          <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">
            {diagnostics.map((diagnostic) => (
              <li key={diagnostic}>{diagnostic}</li>
            ))}
          </ul>
        </DetailSection>
      )}
    </div>
  )
}
