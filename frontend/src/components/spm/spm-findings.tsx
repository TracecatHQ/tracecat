"use client"

import { ShieldAlertIcon } from "lucide-react"
import type {
  SpmControlRead,
  SpmEndpointRead,
  SpmFindingRead,
  SpmInventoryItemRead,
} from "@/client"
import { Button } from "@/components/ui/button"
import {
  type FindingDecision,
  findingStatusVariant,
  formatLabel,
  formatRelativeTimestamp,
  getEndpointName,
  getFindingEnforcementState,
  getInventoryItemPath,
  getInventoryItemRecord,
  severityVariant,
} from "./spm-common"
import { sourceTypeIcon, sourceTypeLabel } from "./spm-icons"
import { FeedRow, SmallBadge } from "./spm-layout"

export function FindingActionButtons(props: {
  busyDecision: { decision: FindingDecision; findingId: string } | null
  finding: SpmFindingRead
  onDecision: (findingId: string, decision: FindingDecision) => Promise<void>
}) {
  const isActive = props.busyDecision?.findingId === props.finding.id
  const canEnforce = props.finding.recommended_action != null

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        className="h-7 px-2 text-xs"
        disabled={props.busyDecision != null}
        onClick={() => void props.onDecision(props.finding.id, "dismiss")}
      >
        {isActive && props.busyDecision?.decision === "dismiss"
          ? "Dismissing..."
          : "Dismiss"}
      </Button>
      <Button
        size="sm"
        className="h-7 px-2 text-xs"
        disabled={props.busyDecision != null || !canEnforce}
        onClick={() => void props.onDecision(props.finding.id, "enforce")}
      >
        {isActive && props.busyDecision?.decision === "enforce"
          ? "Enforcing..."
          : "Enforce"}
      </Button>
    </>
  )
}

export function FindingRow(props: {
  inventoryItems: SpmInventoryItemRead[]
  busyDecision: { decision: FindingDecision; findingId: string } | null
  controls?: SpmControlRead[]
  endpoints: SpmEndpointRead[]
  finding: SpmFindingRead
  onDecision?: (findingId: string, decision: FindingDecision) => Promise<void>
  showEndpoint?: boolean
}) {
  const showEndpoint = props.showEndpoint ?? true
  const item = getInventoryItemRecord(
    props.finding.inventory_item_id,
    props.inventoryItems
  )
  const enforcementState = getFindingEnforcementState(props.finding)
  const controlTitle =
    props.controls?.find((control) => control.id === props.finding.control_id)
      ?.title ?? props.finding.control_key
  const SourceIcon = sourceTypeIcon(props.finding.source_type)
  const subtitleParts = [
    showEndpoint
      ? getEndpointName(props.finding.endpoint_id, props.endpoints)
      : null,
    item?.display_name ?? props.finding.inventory_item_id,
    controlTitle,
  ].filter((part): part is string => Boolean(part))

  return (
    <FeedRow
      icon={<ShieldAlertIcon className="size-4 text-muted-foreground" />}
      title={props.finding.summary}
      subtitle={subtitleParts.join(" · ")}
      badges={
        <>
          <SmallBadge variant={severityVariant(props.finding.severity)}>
            {formatLabel(props.finding.severity)}
          </SmallBadge>
          <SmallBadge variant={findingStatusVariant(props.finding.status)}>
            {formatLabel(props.finding.status)}
          </SmallBadge>
          <SmallBadge variant={enforcementState.variant}>
            {enforcementState.label}
          </SmallBadge>
        </>
      }
      meta={
        <>
          <SmallBadge icon={SourceIcon}>
            {sourceTypeLabel(props.finding.source_type)}
          </SmallBadge>
          <span>{formatRelativeTimestamp(props.finding.updated_at)}</span>
        </>
      }
      actions={
        props.onDecision ? (
          <FindingActionButtons
            busyDecision={props.busyDecision}
            finding={props.finding}
            onDecision={props.onDecision}
          />
        ) : null
      }
    />
  )
}

export function findingMatchesQuery(props: {
  inventoryItems: SpmInventoryItemRead[]
  endpoints: SpmEndpointRead[]
  finding: SpmFindingRead
  query: string
  includesQuery: (
    values: Array<string | null | undefined>,
    query: string
  ) => boolean
}) {
  const item = getInventoryItemRecord(
    props.finding.inventory_item_id,
    props.inventoryItems
  )
  const endpointName = getEndpointName(
    props.finding.endpoint_id,
    props.endpoints
  )
  return props.includesQuery(
    [
      props.finding.summary,
      props.finding.control_id,
      props.finding.control_key,
      props.finding.status,
      props.finding.severity,
      props.finding.item_type,
      props.finding.source_type,
      props.finding.source_location,
      item?.display_name,
      item ? getInventoryItemPath(item) : props.finding.inventory_item_id,
      endpointName,
    ],
    props.query
  )
}
