import {
  formatAgentOutputType,
  parseAgentOutputType,
} from "@/lib/agent-preset-output"

it.each([
  ["str", "str", false],
  ["int", "int", false],
  ["float", "float", false],
  ["bool", "bool", false],
  ["list[str]", "str", true],
  ["list[int]", "int", true],
  ["list[float]", "float", true],
  ["list[bool]", "bool", true],
] as const)(
  "round trips %s through its type and list controls",
  (literal, type, isList) => {
    expect(parseAgentOutputType(literal)).toEqual({ type, isList })
    expect(formatAgentOutputType({ type, isList })).toBe(literal)
  }
)

it.each([undefined, "", "unsupported"])(
  "leaves %p without a selected type",
  (literal) => {
    expect(parseAgentOutputType(literal)).toEqual({
      type: "",
      isList: false,
    })
  }
)
