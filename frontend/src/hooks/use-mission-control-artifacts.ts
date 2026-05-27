import type { UIMessage } from "ai"
import { useCallback, useEffect, useMemo, useState } from "react"
import {
  type ArtifactLane,
  type ArtifactType,
  artifactKey,
  getArtifactDataPayload,
  type MissionControlArtifact,
} from "@/types/mission-control"

/** Derived artifact state for the Mission Control side panel. */
export type UseMissionControlArtifactsResult = {
  artifacts: MissionControlArtifact[]
  lanes: ArtifactLane[]
  activeArtifactKey: string | null
  setActiveArtifactKey: (key: string | null) => void
  closeArtifact: (type: ArtifactType, id: string) => void
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
    const next = new Map<string, MissionControlArtifact>()
    for (const message of messages) {
      for (const part of message.parts ?? []) {
        const payload = getArtifactDataPayload(part)
        if (!payload) {
          continue
        }

        const key = artifactKey(payload.artifact)
        if (closedKeys.has(key)) {
          continue
        }

        if (payload.op === "remove") {
          next.delete(key)
          continue
        }

        next.set(key, payload.artifact)
      }
    }
    return Array.from(next.values())
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
