/** Chat UI variants backed by the shared Tracecat chat stream. */
export type ChatSurface = "regular" | "mission-control"

/** Product capabilities enabled for a chat stream projection. */
export type ChatStreamCapabilities = {
  artifacts: boolean
}

/** Capabilities for each chat surface. Extend this as stream events grow. */
export const CHAT_SURFACE_CAPABILITIES = {
  regular: {
    artifacts: false,
  },
  "mission-control": {
    artifacts: true,
  },
} satisfies Record<ChatSurface, ChatStreamCapabilities>
