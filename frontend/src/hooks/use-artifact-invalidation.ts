import type { ChatOnDataCallback, UIMessage } from "ai"
import { useCallback } from "react"
import { invalidateArtifactQueries } from "@/components/workspace-chat/artifacts/artifact-registry"
import { useQueryClient } from "@/lib/query"
import {
  parseWorkspaceChatArtifactStreamPart,
  type WorkspaceChatArtifactStreamPart,
} from "@/types/workspace-chat-artifacts"

/**
 * Refresh page data when a workspace chat touches an object: each artifact
 * part in the chat stream invalidates the queries its artifact type
 * registers. `handleStreamData` is the chat transport's `onData` callback;
 * `handleArtifactStreamPart` takes a part that is already parsed.
 */
export function useArtifactInvalidation(workspaceId: string) {
  const queryClient = useQueryClient()
  const handleArtifactStreamPart = useCallback(
    (part: WorkspaceChatArtifactStreamPart) =>
      invalidateArtifactQueries(queryClient, workspaceId, part.data.artifact),
    [queryClient, workspaceId]
  )
  const handleStreamData = useCallback<ChatOnDataCallback<UIMessage>>(
    (dataPart) => {
      const part = parseWorkspaceChatArtifactStreamPart(dataPart)
      if (part) {
        handleArtifactStreamPart(part)
      }
    },
    [handleArtifactStreamPart]
  )
  return { handleArtifactStreamPart, handleStreamData }
}
