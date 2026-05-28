import type { UIMessage } from "ai"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  ARTIFACT_DATA_PART_TYPE,
  type ArtifactDataPayload,
  type ArtifactLane,
  type ArtifactType,
  artifactKey,
  parseWorkspaceChatArtifactStreamPart,
  type WorkspaceChatArtifact,
  type WorkspaceChatArtifactStreamPart,
} from "@/types/workspace-chat-artifacts"

/** Derived artifact state for the workspace chat side panel. */
export type UseWorkspaceChatArtifactsResult = {
  artifacts: WorkspaceChatArtifact[]
  lanes: ArtifactLane[]
  activeArtifactKey: string | null
  setActiveArtifactKey: (key: string | null) => void
  closeArtifact: (type: ArtifactType, id: string) => void
  applyStreamPart: (part: WorkspaceChatArtifactStreamPart) => void
}

/** Options for projecting artifact stream parts into workspace chat state. */
export type UseWorkspaceChatArtifactsOptions = {
  enabled?: boolean
  persistedArtifacts?: WorkspaceChatArtifact[]
  onCloseArtifact?: (type: ArtifactType, id: string) => void | Promise<void>
}

type WorkspaceChatArtifactMessageStreamPart = {
  eventKey: string
  part: WorkspaceChatArtifactStreamPart
}

const EMPTY_ARTIFACTS: WorkspaceChatArtifact[] = []

function reduceArtifactPayload(
  next: Map<string, WorkspaceChatArtifact>,
  payload: ArtifactDataPayload
) {
  const key = artifactKey(payload.artifact)
  switch (payload.op) {
    case "upsert":
      next.set(key, payload.artifact)
      return
    case "remove":
      next.delete(key)
      return
  }
}

function reduceWorkspaceChatArtifactStreamPart(
  next: Map<string, WorkspaceChatArtifact>,
  part: WorkspaceChatArtifactStreamPart
) {
  switch (part.type) {
    case ARTIFACT_DATA_PART_TYPE:
      reduceArtifactPayload(next, part.data)
      return
  }
}

/** Project Vercel UI message parts into Workspace chat artifact state. */
export function reduceWorkspaceChatArtifacts(
  messages: UIMessage[]
): WorkspaceChatArtifact[] {
  const next = new Map<string, WorkspaceChatArtifact>()
  for (const { part } of messageStreamParts(messages)) {
    reduceWorkspaceChatArtifactStreamPart(next, part)
  }
  return Array.from(next.values())
}

function messageStreamParts(
  messages: UIMessage[]
): WorkspaceChatArtifactMessageStreamPart[] {
  const streamParts: WorkspaceChatArtifactMessageStreamPart[] = []
  for (const message of messages) {
    for (const [partIndex, rawPart] of (message.parts ?? []).entries()) {
      const part = parseWorkspaceChatArtifactStreamPart(rawPart)
      if (!part) {
        continue
      }
      streamParts.push({
        eventKey: messageStreamPartEventKey(message.id, partIndex, part),
        part,
      })
    }
  }
  return streamParts
}

function messageStreamPartEventKey(
  messageId: string,
  partIndex: number,
  part: WorkspaceChatArtifactStreamPart
): string {
  switch (part.type) {
    case ARTIFACT_DATA_PART_TYPE:
      return [
        messageId,
        partIndex,
        part.type,
        part.data.op,
        artifactKey(part.data.artifact),
        JSON.stringify(part.data.artifact),
      ].join(":")
  }
}

function artifactMapFromArtifacts(
  artifacts: WorkspaceChatArtifact[]
): Map<string, WorkspaceChatArtifact> {
  const next = new Map<string, WorkspaceChatArtifact>()
  for (const artifact of artifacts) {
    next.set(artifactKey(artifact), artifact)
  }
  return next
}

function artifactMapFromArtifactsAndMessageStreamParts(
  artifacts: WorkspaceChatArtifact[],
  streamParts: WorkspaceChatArtifactMessageStreamPart[]
): Map<string, WorkspaceChatArtifact> {
  const next = artifactMapFromArtifacts(artifacts)
  for (const { part } of streamParts) {
    reduceWorkspaceChatArtifactStreamPart(next, part)
  }
  return next
}

function lastUpsertKey(
  streamParts: WorkspaceChatArtifactMessageStreamPart[]
): string | null {
  let nextKey: string | null = null
  for (const { part } of streamParts) {
    switch (part.type) {
      case ARTIFACT_DATA_PART_TYPE:
        if (part.data.op === "upsert") {
          nextKey = artifactKey(part.data.artifact)
        }
        break
    }
  }
  return nextKey
}

function lastArtifactKey(artifacts: WorkspaceChatArtifact[]): string | null {
  const artifact = artifacts.at(-1)
  return artifact ? artifactKey(artifact) : null
}

function artifactSignature(artifacts: WorkspaceChatArtifact[]): string {
  return artifacts
    .map((artifact) => `${artifactKey(artifact)}:${JSON.stringify(artifact)}`)
    .join("|")
}

/** Derive Workspace chat artifacts from Vercel UI message data parts. */
export function useWorkspaceChatArtifacts(
  messages: UIMessage[],
  options: UseWorkspaceChatArtifactsOptions = {}
): UseWorkspaceChatArtifactsResult {
  const enabled = options.enabled ?? true
  const persistedArtifacts = options.persistedArtifacts ?? EMPTY_ARTIFACTS
  const persistedArtifactSignature = useMemo(
    () => artifactSignature(persistedArtifacts),
    [persistedArtifacts]
  )
  const processedMessagePartKeysRef = useRef<Set<string>>(new Set())
  const previousEnabledRef = useRef(enabled)
  const previousFirstMessageIdRef = useRef<string | null>(
    messages[0]?.id ?? null
  )
  const previousPersistedArtifactSignatureRef = useRef(
    persistedArtifactSignature
  )
  const [activeArtifactKey, setActiveArtifactKey] = useState<string | null>(
    () => {
      if (!enabled) {
        return null
      }
      const initialParts = messageStreamParts(messages)
      return lastUpsertKey(initialParts) ?? lastArtifactKey(persistedArtifacts)
    }
  )
  const [streamArtifacts, setStreamArtifacts] = useState<
    Map<string, WorkspaceChatArtifact>
  >(() => {
    if (!enabled) {
      return new Map()
    }
    const initialParts = messageStreamParts(messages)
    for (const { eventKey } of initialParts) {
      processedMessagePartKeysRef.current.add(eventKey)
    }
    return artifactMapFromArtifactsAndMessageStreamParts(
      persistedArtifacts,
      initialParts
    )
  })

  useEffect(() => {
    const firstMessageId = messages[0]?.id ?? null
    const enabledChanged = previousEnabledRef.current !== enabled
    previousEnabledRef.current = enabled
    const firstMessageChanged =
      previousFirstMessageIdRef.current !== firstMessageId
    previousFirstMessageIdRef.current = firstMessageId
    const persistedArtifactsChanged =
      previousPersistedArtifactSignatureRef.current !==
      persistedArtifactSignature
    previousPersistedArtifactSignatureRef.current = persistedArtifactSignature

    if (!enabled) {
      processedMessagePartKeysRef.current.clear()
      setStreamArtifacts((current) =>
        current.size === 0 ? current : new Map()
      )
      setActiveArtifactKey((current) => (current === null ? current : null))
      return
    }

    const currentParts = messageStreamParts(messages)
    if (enabledChanged || firstMessageChanged) {
      processedMessagePartKeysRef.current = new Set(
        currentParts.map(({ eventKey }) => eventKey)
      )
      setStreamArtifacts(
        artifactMapFromArtifactsAndMessageStreamParts(
          persistedArtifacts,
          currentParts
        )
      )
      setActiveArtifactKey(
        lastUpsertKey(currentParts) ?? lastArtifactKey(persistedArtifacts)
      )
      return
    }

    const pendingParts = currentParts.filter(
      ({ eventKey }) => !processedMessagePartKeysRef.current.has(eventKey)
    )

    if (persistedArtifactsChanged) {
      for (const { eventKey } of pendingParts) {
        processedMessagePartKeysRef.current.add(eventKey)
      }
      setStreamArtifacts(
        artifactMapFromArtifactsAndMessageStreamParts(
          persistedArtifacts,
          pendingParts
        )
      )
      const nextActiveArtifactKey =
        lastUpsertKey(pendingParts) ?? lastArtifactKey(persistedArtifacts)
      setActiveArtifactKey(nextActiveArtifactKey)
      return
    }

    if (pendingParts.length === 0) {
      return
    }

    for (const { eventKey } of pendingParts) {
      processedMessagePartKeysRef.current.add(eventKey)
    }
    setStreamArtifacts((current) => {
      const next = new Map(current)
      for (const { part } of pendingParts) {
        reduceWorkspaceChatArtifactStreamPart(next, part)
      }
      return next
    })

    const nextActiveArtifactKey = lastUpsertKey(pendingParts)
    if (nextActiveArtifactKey) {
      setActiveArtifactKey(nextActiveArtifactKey)
    }
  }, [enabled, messages, persistedArtifactSignature, persistedArtifacts])

  const artifacts = useMemo(() => {
    if (!enabled) {
      return []
    }
    return Array.from(streamArtifacts.values())
  }, [enabled, streamArtifacts])

  const applyStreamPart = useCallback(
    (part: WorkspaceChatArtifactStreamPart) => {
      if (!enabled) {
        return
      }

      setStreamArtifacts((current) => {
        const next = new Map(current)
        reduceWorkspaceChatArtifactStreamPart(next, part)
        return next
      })

      switch (part.type) {
        case ARTIFACT_DATA_PART_TYPE:
          if (part.data.op === "upsert") {
            setActiveArtifactKey(artifactKey(part.data.artifact))
          }
          return
      }
    },
    [enabled]
  )

  const lanes = useMemo(() => {
    const next = new Map<string, ArtifactLane>()
    for (const artifact of artifacts) {
      const scope = artifact.scope
      const laneKey = `${scope?.agentType ?? "main"}:${scope?.agentId ?? "main"}`
      const lane = next.get(laneKey)
      if (lane) {
        lane.artifacts.push(artifact)
        continue
      }
      next.set(laneKey, {
        agentType: scope?.agentType,
        agentId: scope?.agentId,
        artifacts: [artifact],
      })
    }
    return Array.from(next.values())
  }, [artifacts])

  useEffect(() => {
    if (artifacts.length === 0) {
      if (activeArtifactKey !== null) {
        setActiveArtifactKey(null)
      }
      return
    }

    if (
      activeArtifactKey &&
      artifacts.some((artifact) => artifactKey(artifact) === activeArtifactKey)
    ) {
      return
    }

    setActiveArtifactKey(artifactKey(artifacts[artifacts.length - 1]))
  }, [activeArtifactKey, artifacts])

  const closeArtifact = useCallback(
    (type: ArtifactType, id: string) => {
      const key = `${type}:${id}`
      setStreamArtifacts((current) => {
        if (!current.has(key)) {
          return current
        }
        const next = new Map(current)
        next.delete(key)
        return next
      })
      if (activeArtifactKey === key) {
        const fallback = artifacts.find(
          (artifact) => artifactKey(artifact) !== key
        )
        setActiveArtifactKey(fallback ? artifactKey(fallback) : null)
      }
      void options.onCloseArtifact?.(type, id)
    },
    [activeArtifactKey, artifacts, options.onCloseArtifact]
  )

  return {
    artifacts,
    lanes,
    activeArtifactKey,
    setActiveArtifactKey,
    closeArtifact,
    applyStreamPart,
  }
}
