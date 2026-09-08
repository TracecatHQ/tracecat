import {
  type AgentPresetToolPolicyPreview,
  agentPresetsPreviewToolPolicy,
} from "@/client"
import { useQuery } from "@/lib/query"

/** Evaluate unsaved selections with the policy pipeline used by preset runtime. */
export function useAgentPresetToolPolicy(
  workspaceId: string,
  selections: AgentPresetToolPolicyPreview
) {
  return useQuery({
    queryKey: ["agent-preset-tool-policy", workspaceId, selections],
    queryFn: () =>
      agentPresetsPreviewToolPolicy({
        workspaceId,
        requestBody: selections,
      }),
    enabled: Boolean(workspaceId),
    retry: false,
  })
}
