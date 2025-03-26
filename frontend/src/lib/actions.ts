export const SUBFLOW_ACTION_TYPES = ["core.workflow.execute"] as const
export type SubflowActionType = (typeof SUBFLOW_ACTION_TYPES)[number]

export function actionTypeToNodeTypename(type: string): "subflow" | "udf" {
  if (SUBFLOW_ACTION_TYPES.includes(type as SubflowActionType)) return "subflow"
  return "udf"
}
