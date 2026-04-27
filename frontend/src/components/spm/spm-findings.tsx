"use client"

import { ShieldAlertIcon } from "lucide-react"
import type {
  SpmAssetRead,
  SpmControlRead,
  SpmEndpointRead,
  SpmFindingRead,
} from "@/client"
import { Button } from "@/components/ui/button"
import {
  type FindingDecision,
  findingStatusVariant,
  formatLabel,
  formatRelativeTimestamp,
  getAssetPath,
  getAssetRecord,
  getEndpointName,
  getFindingEnforcementState,
  severityVariant,
} from "./spm-common"
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
  assets: SpmAssetRead[]
  busyDecision: { decision: FindingDecision; findingId: string } | null
  controls?: SpmControlRead[]
  endpoints: SpmEndpointRead[]
  finding: SpmFindingRead
  onDecision?: (findingId: string, decision: FindingDecision) => Promise<void>
  showEndpoint?: boolean
}) {
  const showEndpoint = props.showEndpoint ?? true
  const asset = getAssetRecord(props.finding.asset_id, props.assets)
  const enforcementState = getFindingEnforcementState(props.finding)
  const controlTitle =
    props.controls?.find((control) => control.id === props.finding.control_id)
      ?.title ?? props.finding.control_key
  const subtitleParts = [
    showEndpoint
      ? getEndpointName(props.finding.endpoint_id, props.endpoints)
      : null,
    asset?.display_name ?? props.finding.asset_id,
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
          <span>{formatLabel(props.finding.asset_class)}</span>
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
  assets: SpmAssetRead[]
  endpoints: SpmEndpointRead[]
  finding: SpmFindingRead
  query: string
  includesQuery: (
    values: Array<string | null | undefined>,
    query: string
  ) => boolean
}) {
  const asset = getAssetRecord(props.finding.asset_id, props.assets)
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
      props.finding.asset_type,
      props.finding.asset_class,
      asset?.display_name,
      asset ? getAssetPath(asset) : props.finding.asset_id,
      endpointName,
    ],
    props.query
  )
}
