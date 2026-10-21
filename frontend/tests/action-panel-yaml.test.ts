import YAML from "yaml"
import { reconstructYamlFromForm } from "@/components/builder/panel/action-panel"

const original =
  "prompt: Old\nactions: []\nmcp_integrations: []\ntool_approvals: {}\n"

it("round-trips unified agent tool values through action YAML", () => {
  const values = {
    prompt: "Investigate",
    actions: ["tools.alpha.first", "unknown.saved"],
    mcp_integrations: ["mcp-test"],
    tool_approvals: { "tools.alpha.first": true },
  }
  const yaml = reconstructYamlFromForm(original, values)
  expect(YAML.parse(yaml)).toEqual(values)
})

it("keeps the original key order and appends new keys", () => {
  const yaml = reconstructYamlFromForm(original, {
    model_settings: { temperature: 0 },
    tool_approvals: { "tools.alpha.first": true },
    mcp_integrations: ["mcp-test"],
    actions: ["tools.alpha.first"],
    prompt: "Investigate",
  })
  expect(yaml).toBe(
    [
      "prompt: Investigate",
      "actions:",
      "  - tools.alpha.first",
      "mcp_integrations:",
      "  - mcp-test",
      "tool_approvals:",
      "  tools.alpha.first: true",
      "model_settings:",
      "  temperature: 0",
      "",
    ].join("\n")
  )
})

it("emits no key for siblings the Tools field cleared to undefined", () => {
  const yaml = reconstructYamlFromForm(original, {
    prompt: "Investigate",
    actions: ["tools.alpha.first"],
    mcp_integrations: undefined,
    tool_approvals: undefined,
  })
  expect(yaml).toBe("prompt: Investigate\nactions:\n  - tools.alpha.first\n")
  expect(yaml).not.toContain("mcp_integrations")
  expect(yaml).not.toContain("tool_approvals")
})
