import { isMcpProvider } from "@/lib/integrations"

describe("integration helpers", () => {
  it("classifies platform MCP providers without hiding custom OAuth providers", () => {
    expect(isMcpProvider("github_mcp")).toBe(true)
    expect(isMcpProvider("custom_acme_mcp")).toBe(false)
    expect(isMcpProvider("custom_acme")).toBe(false)
  })
})
