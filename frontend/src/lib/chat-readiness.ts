/** A title and explanation for a chat that has no usable model. */
export interface ChatModelNotice {
  title: string
  message: string
}

/** Shown when the organization has no usable default model. */
export const NO_DEFAULT_MODEL_MESSAGE =
  "Select a default model in agent settings to enable chat."

/**
 * Explain a chat that has no usable model because of the workspace's model
 * subset, which organization agent settings alone cannot fix. Returns null for
 * every other readiness reason.
 */
export function getWorkspaceModelNotice(
  reason: string | undefined
): ChatModelNotice | null {
  switch (reason) {
    case "subset_disabled":
      return {
        title: "Workspace models disabled",
        message:
          "The organization disabled every model enabled for this workspace. A workspace admin can choose new ones in workspace AI model settings, or an organization admin can enable more models.",
      }
    case "default_not_enabled":
      return {
        title: "Default model not enabled",
        message:
          "The organization default model is not enabled for this workspace. A workspace admin can enable it in workspace AI model settings.",
      }
    default:
      return null
  }
}

/** One-line reason a chat has no usable model, for a disabled composer. */
export function getModelUnavailableMessage(reason: string | undefined): string {
  return getWorkspaceModelNotice(reason)?.message ?? NO_DEFAULT_MODEL_MESSAGE
}
