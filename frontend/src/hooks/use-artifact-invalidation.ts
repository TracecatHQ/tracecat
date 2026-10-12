import type { ChatOnDataCallback, UIMessage } from "ai"
import { useCallback } from "react"
import { invalidateArtifactQueries } from "@/components/workspace-chat/artifacts/artifact-registry"
import { useQueryClient } from "@/lib/query"
import {
  ARTIFACT_DATA_PART_TYPE,
  parseWorkspaceChatArtifactStreamPart,
  type WorkspaceChatArtifactStreamPart,
} from "@/types/workspace-chat-artifacts"

/**
 * Refresh page data when a workspace chat touches an object.
 *
 * The chat stream carries an artifact part for every case, table, workflow,
 * agent or form a tool read or changed. Each part invalidates the queries its
 * artifact type registers, so whatever page is showing that object refetches.
 *
 * - `handleStreamData` is the chat transport's `onData` callback.
 * - `handleArtifactStreamPart` takes a part that is already parsed.
 */
export function useArtifactInvalidation(workspaceId: string) {
  const queryClient = useQueryClient()
  const handleArtifactStreamPart = useCallback(
    (part: WorkspaceChatArtifactStreamPart) => {
      switch (part.type) {
        case ARTIFACT_DATA_PART_TYPE:
          invalidateArtifactQueries(
            queryClient,
            workspaceId,
            part.data.artifact
          )
          return
      }
    },
    [queryClient, workspaceId]
  )
  const handleStreamData = useCallback<ChatOnDataCallback<UIMessage>>(
    (dataPart) => {
      const part = parseWorkspaceChatArtifactStreamPart(dataPart)
      if (!part) {
        return
      }
      handleArtifactStreamPart(part)
    },
    [handleArtifactStreamPart]
  )
  return { handleArtifactStreamPart, handleStreamData }
}
