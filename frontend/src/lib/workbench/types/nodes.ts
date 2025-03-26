import type { Schedule, WebhookRead } from "@/client"
import type { Node, XYPosition } from "@xyflow/react"

export enum NodeTypename {
  Action = "udf",
  Trigger = "trigger",
  Subflow = "subflow",
  Selector = "selector",
}

export type ActionNodeData = {
  id: string
  type: string // alias for key
  position: XYPosition
  /*
   * The ID of the subflow that this action node is associated with.
   * This is used to link the action node to the subflow node in the UI.
   */
  subflowId?: string
  subflowAlias?: string
  isConfigured: boolean

  // Allow any additional properties from legacy data
  [key: string]: unknown
}
export type ActionNodeType = Node<ActionNodeData, NodeTypename.Action>

export type SubflowNodeData = {
  id: string
  type: string // alias for key
  position: XYPosition
  /*
   * The ID of the workflow that this subflow node is associated with.
   * This is used to link the subflow node to the workflow node in the UI.
   */
  subflowId?: string
  isExpanded: boolean
  toggleExpand: (expanded: boolean) => void
}
export type SubflowNodeType = Node<SubflowNodeData, NodeTypename.Subflow>

export type TriggerNodeData = {
  type: "trigger"
  title: string
  status: "online" | "offline"
  isConfigured: boolean
  entrypointId?: string
  webhook: WebhookRead
  schedules: Schedule[]
}
export type TriggerNodeType = Node<TriggerNodeData, NodeTypename.Trigger>

export type SelectorNodeData = {
  type: "selector"
}
export type SelectorNodeType = Node<SelectorNodeData, NodeTypename.Selector>

export type NodeType =
  | ActionNodeType
  | TriggerNodeType
  | SelectorNodeType
  | SubflowNodeType

export type NodeData =
  | ActionNodeData
  | TriggerNodeData
  | SelectorNodeData
  | SubflowNodeData

export const invincibleNodeTypes: readonly string[] = [NodeTypename.Trigger]
export const ephemeralNodeTypes: readonly string[] = [NodeTypename.Selector]
