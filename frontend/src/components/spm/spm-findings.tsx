"use client"

import { ShieldAlertIcon } from "lucide-react"
import type {
  SpmControlRead,
  SpmEndpointRead,
  SpmFindingRead,
  SpmInventoryItemRead,
} from "@/client"
import {
  findingStatusVariant,
  formatLabel,
  formatRelativeTimestamp,
  getEndpointName,
  getInventoryItemPath,
  getInventoryItemRecord,
  severityVariant,
} from "./spm-common"
import { sourceTypeIcon, sourceTypeLabel } from "./spm-icons"
import { FeedRow, SmallBadge } from "./spm-layout"

export function FindingRow(props: {
  inventoryItems: SpmInventoryItemRead[]
  controls?: SpmControlRead[]
  endpoints: SpmEndpointRead[]
  finding: SpmFindingRead
  showEndpoint?: boolean
}) {
  const showEndpoint = props.showEndpoint ?? true
  const item = getInventoryItemRecord(
    props.finding.inventory_item_id,
    props.inventoryItems
  )
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
