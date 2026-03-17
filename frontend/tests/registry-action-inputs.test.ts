import { normalizeRegistryActionInputs } from "@/lib/registry-action-inputs"

describe("normalizeRegistryActionInputs", () => {
  it("maps legacy AI action model fields to a composite model selection", () => {
    const normalized = normalizeRegistryActionInputs("ai.agent", {
      model_name: "gpt-5",
      model_provider: "openai",
      source_id: "11111111-1111-1111-1111-111111111111",
      user_prompt: "hello",
    })

    expect(normalized).toEqual({
      model: JSON.stringify([
        "11111111-1111-1111-1111-111111111111",
        "openai",
        "gpt-5",
      ]),
      user_prompt: "hello",
    })
  })

  it("leaves non-AI actions unchanged", () => {
    const inputs = {
      model_name: "gpt-5",
      model_provider: "openai",
    }

    expect(normalizeRegistryActionInputs("core.http_request", inputs)).toBe(
      inputs
    )
  })

  it("preserves already-normalized model selections", () => {
    const inputs = {
      model: JSON.stringify([null, "openai", "gpt-5"]),
      user_prompt: "hello",
    }

    expect(normalizeRegistryActionInputs("ai.action", inputs)).toBe(inputs)
  })
})
