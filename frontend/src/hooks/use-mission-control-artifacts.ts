import type { UIMessage } from "ai"
import { useCallback, useEffect, useMemo, useState } from "react"
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
}

function reduceArtifactPayload(
  next: Map<string, MissionControlArtifact>,
  payload: ArtifactDataPayload,
  closedKeys: ReadonlySet<string>
) {
  const key = artifactKey(payload.artifact)
  switch (payload.op) {
    case "upsert":
      if (!closedKeys.has(key)) {
        next.set(key, payload.artifact)
      }
      return
    case "remove":
      next.delete(key)
      return
  }
}

function reduceMissionControlStreamPart(
  next: Map<string, MissionControlArtifact>,
  part: MissionControlStreamPart,
  closedKeys: ReadonlySet<string>
) {
  switch (part.type) {
    case ARTIFACT_DATA_PART_TYPE:
      reduceArtifactPayload(next, part.data, closedKeys)
      return
  }
}

/** Project Vercel UI message parts into Mission Control artifact state. */
export function reduceMissionControlArtifacts(
  messages: UIMessage[],
  closedKeys: ReadonlySet<string>
): MissionControlArtifact[] {
  const next = new Map<string, MissionControlArtifact>()
  for (const message of messages) {
    for (const part of message.parts ?? []) {
      const streamPart = parseMissionControlStreamPart(part)
      if (!streamPart) {
        continue
      }
      reduceMissionControlStreamPart(next, streamPart, closedKeys)
    }
  }
  return Array.from(next.values())
}

/** Derive Mission Control artifacts from Vercel UI message data parts. */
export function useMissionControlArtifacts(
  messages: UIMessage[]
): UseMissionControlArtifactsResult {
  const [closedKeys, setClosedKeys] = useState<Set<string>>(() => new Set())
  const [activeArtifactKey, setActiveArtifactKey] = useState<string | null>(
    null
  )

  const artifacts = useMemo(() => {
    return reduceMissionControlArtifacts(messages, closedKeys)
  }, [closedKeys, messages])

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
      setClosedKeys((current) => {
        const next = new Set(current)
        next.add(key)
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
  }
}
