import { buildChatReadinessOptions } from "@/lib/chat-readiness"

describe("buildChatReadinessOptions", () => {
  it("uses the raw session selection when the resolved catalog model is unavailable", () => {
    expect(
      buildChatReadinessOptions({
        workspaceId: "workspace-1",
        selection: {
          source_id: "source-1",
          model_provider: "openai_compatible_gateway",
          model_name: "qwen-unavailable",
        },
      })
    ).toEqual({
      workspaceId: "workspace-1",
      selection: {
        source_id: "source-1",
        model_provider: "openai_compatible_gateway",
        model_name: "qwen-unavailable",
      },
    })
  })

  it("prefers preset selection over a raw session selection", () => {
    expect(
      buildChatReadinessOptions({
        workspaceId: "workspace-1",
        preset: {
          source_id: "preset-source",
          model_provider: "anthropic",
          model_name: "claude-sonnet-4-5",
        },
        selection: {
          source_id: "session-source",
          model_provider: "openai",
          model_name: "gpt-5.2",
        },
      })
    ).toEqual({
      workspaceId: "workspace-1",
      selection: {
        source_id: "preset-source",
        model_provider: "anthropic",
        model_name: "claude-sonnet-4-5",
      },
    })
  })
})
