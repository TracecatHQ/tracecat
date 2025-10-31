export type DefaultWorkspacePreference =
  | {
      strategy: "last_viewed"
    }
  | {
      strategy: "specific"
      workspaceId: string | null
    }

const DEFAULT_WORKSPACE_PREFERENCE: DefaultWorkspacePreference = {
  strategy: "last_viewed",
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

export function getDefaultWorkspacePreference(
  settings?: Record<string, unknown>
): DefaultWorkspacePreference {
  if (!isRecord(settings)) {
    return DEFAULT_WORKSPACE_PREFERENCE
  }

  const defaultWorkspace = settings.default_workspace

  if (isRecord(defaultWorkspace)) {
    const strategy = defaultWorkspace.strategy
    const workspaceId = defaultWorkspace.workspace_id

    if (strategy === "specific") {
      return {
        strategy: "specific",
        workspaceId:
          typeof workspaceId === "string" && workspaceId.length > 0
            ? workspaceId
            : null,
      }
    }

    if (strategy === "last_viewed") {
      return DEFAULT_WORKSPACE_PREFERENCE
    }

    if (typeof workspaceId === "string") {
      return {
        strategy: "specific",
        workspaceId,
      }
    }
  }

  const legacyDefaultWorkspaceId = settings.default_workspace_id
  if (typeof legacyDefaultWorkspaceId === "string") {
    return {
      strategy: "specific",
      workspaceId: legacyDefaultWorkspaceId,
    }
  }

  return DEFAULT_WORKSPACE_PREFERENCE
}

export function withDefaultWorkspacePreference(
  settings: Record<string, unknown> | undefined,
  preference: DefaultWorkspacePreference
): Record<string, unknown> {
  const nextSettings: Record<string, unknown> = isRecord(settings)
    ? { ...settings }
    : {}

  nextSettings.default_workspace =
    preference.strategy === "specific"
      ? {
          strategy: "specific",
          workspace_id: preference.workspaceId ?? null,
        }
      : {
          strategy: "last_viewed",
        }

  if (
    "default_workspace_id" in nextSettings &&
    preference.strategy === "last_viewed"
  ) {
    delete nextSettings.default_workspace_id
  }

  if (preference.strategy === "specific") {
    nextSettings.default_workspace_id = preference.workspaceId ?? null
  }

  return nextSettings
}
