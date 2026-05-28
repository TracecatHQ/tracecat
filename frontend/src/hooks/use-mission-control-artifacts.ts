import type { UIMessage } from "ai"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  ARTIFACT_DATA_PART_TYPE,
  type ArtifactDataPayload,
  type ArtifactLane,
  type ArtifactType,
  artifactKey,
  type MissionControlArtifact,
  type MissionControlStreamPart,
  parseMissionControlStreamPart,
} from "@/types/mission-control"

/** Derived artifact state for the Mission Control side panel. */
export type UseMissionControlArtifactsResult = {
  artifacts: MissionControlArtifact[]
  lanes: ArtifactLane[]
  activeArtifactKey: string | null
  setActiveArtifactKey: (key: string | null) => void
  closeArtifact: (type: ArtifactType, id: string) => void
  applyStreamPart: (part: MissionControlStreamPart) => void
}

/** Options for projecting artifact stream parts into Mission Control state. */
export type UseMissionControlArtifactsOptions = {
  enabled?: boolean
}

type MissionControlMessageStreamPart = {
  eventKey: string
  part: MissionControlStreamPart
}

function reduceArtifactPayload(
  next: Map<string, MissionControlArtifact>,
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

function reduceMissionControlStreamPart(
  next: Map<string, MissionControlArtifact>,
  part: MissionControlStreamPart
) {
  switch (part.type) {
    case ARTIFACT_DATA_PART_TYPE:
      reduceArtifactPayload(next, part.data)
      return
  }
}

/** Project Vercel UI message parts into Mission Control artifact state. */
export function reduceMissionControlArtifacts(
  messages: UIMessage[]
): MissionControlArtifact[] {
  const next = new Map<string, MissionControlArtifact>()
  for (const { part } of messageStreamParts(messages)) {
    reduceMissionControlStreamPart(next, part)
  }
  return Array.from(next.values())
}

function messageStreamParts(
  messages: UIMessage[]
): MissionControlMessageStreamPart[] {
  const streamParts: MissionControlMessageStreamPart[] = []
  for (const message of messages) {
    for (const [partIndex, rawPart] of (message.parts ?? []).entries()) {
      const part = parseMissionControlStreamPart(rawPart)
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
  part: MissionControlStreamPart
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

function artifactMapFromMessageStreamParts(
  streamParts: MissionControlMessageStreamPart[]
): Map<string, MissionControlArtifact> {
  const next = new Map<string, MissionControlArtifact>()
  for (const { part } of streamParts) {
    reduceMissionControlStreamPart(next, part)
  }
  return next
}

function lastUpsertKey(
  streamParts: MissionControlMessageStreamPart[]
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

/** Derive Mission Control artifacts from Vercel UI message data parts. */
export function useMissionControlArtifacts(
  messages: UIMessage[],
  options: UseMissionControlArtifactsOptions = {}
): UseMissionControlArtifactsResult {
  const enabled = options.enabled ?? true
  const processedMessagePartKeysRef = useRef<Set<string>>(new Set())
  const previousFirstMessageIdRef = useRef<string | null>(
    messages[0]?.id ?? null
  )
  const [activeArtifactKey, setActiveArtifactKey] = useState<string | null>(
    null
  )
  const [streamArtifacts, setStreamArtifacts] = useState<
    Map<string, MissionControlArtifact>
  >(() => {
    if (!enabled) {
      return new Map()
    }
    const initialParts = messageStreamParts(messages)
    for (const { eventKey } of initialParts) {
      processedMessagePartKeysRef.current.add(eventKey)
    }
    return artifactMapFromMessageStreamParts(initialParts)
  })

  useEffect(() => {
    const firstMessageId = messages[0]?.id ?? null
    const firstMessageChanged =
      previousFirstMessageIdRef.current !== firstMessageId
    previousFirstMessageIdRef.current = firstMessageId

    if (!enabled) {
      processedMessagePartKeysRef.current.clear()
      setStreamArtifacts(new Map())
      setActiveArtifactKey(null)
      return
    }

    const currentParts = messageStreamParts(messages)
    if (firstMessageChanged) {
      processedMessagePartKeysRef.current = new Set(
        currentParts.map(({ eventKey }) => eventKey)
      )
      setStreamArtifacts(artifactMapFromMessageStreamParts(currentParts))
      setActiveArtifactKey(lastUpsertKey(currentParts))
      return
    }

    const pendingParts = currentParts.filter(
      ({ eventKey }) => !processedMessagePartKeysRef.current.has(eventKey)
    )
    if (pendingParts.length === 0) {
      return
    }

    for (const { eventKey } of pendingParts) {
      processedMessagePartKeysRef.current.add(eventKey)
    }
    setStreamArtifacts((current) => {
      const next = new Map(current)
      for (const { part } of pendingParts) {
        reduceMissionControlStreamPart(next, part)
      }
      return next
    })

    const nextActiveArtifactKey = lastUpsertKey(pendingParts)
    if (nextActiveArtifactKey) {
      setActiveArtifactKey(nextActiveArtifactKey)
    }
  }, [enabled, messages])

  const artifacts = useMemo(() => {
    if (!enabled) {
      return []
    }
    return Array.from(streamArtifacts.values())
  }, [enabled, streamArtifacts])

  const applyStreamPart = useCallback(
    (part: MissionControlStreamPart) => {
      if (!enabled) {
        return
      }

      setStreamArtifacts((current) => {
        const next = new Map(current)
        reduceMissionControlStreamPart(next, part)
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
    },
    [activeArtifactKey, artifacts]
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
