import { parseYaml, stringifyYaml } from "@/lib/yaml"

describe("PyYAML-compatible YAML helpers", () => {
  it.each(["on", "off", "yes", "no", "On", "OFF", "Yes", "NO"])(
    "keeps the quoted string %s quoted when re-serialized",
    (word) => {
      const parsed = parseYaml(`value: "${word}"`)
      expect(parsed).toEqual({ value: word })
      expect(stringifyYaml(parsed)).toBe(`value: "${word}"\n`)
    }
  )

  it("parses plain YAML 1.1 booleans like PyYAML", () => {
    expect(parseYaml("a: on\nb: off\nc: yes\nd: no\ne: true")).toEqual({
      a: true,
      b: false,
      c: true,
      d: false,
      e: true,
    })
  })

  it("keeps y/n and dates as strings like the backend loader", () => {
    const parsed = parseYaml("a: y\nb: n\nc: 2024-01-01")
    expect(parsed).toEqual({ a: "y", b: "n", c: "2024-01-01" })
    expect(stringifyYaml(parsed)).toBe("a: y\nb: n\nc: 2024-01-01\n")
  })

  it("round-trips nested action inputs", () => {
    const src =
      'mode: "off"\nflags:\n  - "yes"\n  - true\nheaders:\n  X-Debug: "on"\n'
    expect(stringifyYaml(parseYaml(src))).toBe(src)
  })
})
