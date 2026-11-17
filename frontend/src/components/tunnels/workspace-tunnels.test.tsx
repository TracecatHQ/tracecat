import { parseTunnelTargets } from "@/components/tunnels/workspace-tunnels"

describe("parseTunnelTargets", () => {
  it("parses one host per line with one or more ports", () => {
    expect(
      parseTunnelTargets("splunk.internal:8089\n\n 10.20.0.15:443, 8443 \n")
    ).toEqual([
      { host: "splunk.internal", ports: [8089] },
      { host: "10.20.0.15", ports: [443, 8443] },
    ])
  })

  it("rejects lines without a port", () => {
    expect(() => parseTunnelTargets("splunk.internal")).toThrow("Line 1")
  })

  it("rejects out-of-range ports", () => {
    expect(() => parseTunnelTargets("a:1\nb:70000")).toThrow(
      "Line 2: ports must be between 1 and 65535"
    )
  })

  it("requires at least one target", () => {
    expect(() => parseTunnelTargets("  \n")).toThrow("Add at least one target")
  })
})
