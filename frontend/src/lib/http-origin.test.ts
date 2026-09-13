import { normalizeHttpOrigin } from "@/lib/http-origin"

describe("normalizeHttpOrigin", () => {
  it.each([
    [
      "https://COLLECTOR.example.com.:443/v1/logs",
      "https://collector.example.com",
    ],
    [
      "https://collector.example.com.../v1/logs",
      "https://collector.example.com",
    ],
    ["https://täst.example./v1/logs", "https://xn--tst-qla.example"],
    [
      "http://collector.example.com.:80/v1/logs",
      "http://collector.example.com",
    ],
    ["https://[2001:db8::1]:8443/v1/logs", "https://[2001:db8::1]:8443"],
  ])("normalizes %s", (value, expected) => {
    expect(normalizeHttpOrigin(value)).toBe(expected)
  })

  it.each(["ftp://collector.example.com", "https://./path", "not a URL"])(
    "rejects %s",
    (value) => {
      expect(normalizeHttpOrigin(value)).toBeNull()
    }
  )
})
