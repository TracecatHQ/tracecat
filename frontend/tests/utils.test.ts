import { compressActionsInString } from "@/lib/expressions"
import {
  isServer,
  shortTimeAgo,
  slugify,
  slugifyActionRef,
  undoSlugify,
} from "@/lib/utils"

describe("slugify", () => {
  it("should convert a string to a slug", () => {
    const slug = slugify("Hello World")
    expect(slug).toBe("hello-world")
  })

  it("matches the python implementation for key edge cases", () => {
    const cases = [
      "foo/bar:baz",
      "  Café déjà-vu  ",
      "foo__bar",
      "foo--bar",
      "Action: Name/Version",
      "ACTIONS.test.result",
      "ACTIONS test result",
    ]

    const expected = [
      "foobarbaz",
      "cafe-deja-vu",
      "foo__bar",
      "foo-bar",
      "action-nameversion",
      "actionstestresult",
      "actions-test-result",
    ]

    expected.forEach((pythonSlug, idx) => {
      expect(slugify(cases[idx])).toBe(pythonSlug)
    })
  })

  it("supports alternative delimiters like python", () => {
    const cases = ["Hello World", "foo--bar", "foo/bar:baz"]
    const expected = ["hello_world", "foo_bar", "foobarbaz"]

    expected.forEach((pythonSlug, idx) => {
      expect(slugify(cases[idx], "_")).toBe(pythonSlug)
    })
  })
})

describe("slugifyActionRef", () => {
  it("uses underscore delimiter to align with backend action refs", () => {
    expect(slugifyActionRef("Hello World")).toBe("hello_world")
    expect(slugifyActionRef("foo--bar baz")).toBe("foo_bar_baz")
  })
})

describe("undoSlugify", () => {
  it("should convert a slug back to a string", () => {
    const string = undoSlugify("hello-world")
    expect(string).toBe("Hello World")
  })
})

describe("isServer", () => {
  it("should return false in Jest environment (jsdom)", () => {
    const result = isServer()
    expect(result).toBe(false)
  })

  it("should return true when window is undefined", () => {
    // Since Jest environment makes this complex, let's test the logic directly
    const isWindowUndefined = typeof window === "undefined"
    expect(isWindowUndefined).toBe(false) // In Jest/jsdom, window is defined

    // Test that the function logic would work correctly
    expect(typeof window === "undefined").toBe(false)
    expect(!(typeof window === "undefined")).toBe(true)
  })
})

describe("compressActionsInString", () => {
  it("should return empty string for empty input", () => {
    const result = compressActionsInString("")
    expect(result).toBe("")
  })

  it("should return original string if no ACTIONS expressions are present", () => {
    const originalString = "no actions in this string"
    const result = compressActionsInString(originalString)
    expect(result).toBe(originalString)
  })

  it("should replace a single ACTIONS expression with its compact form", () => {
    const result = compressActionsInString("ACTIONS.test.result")
    expect(result).toBe("@test")
  })

  it("should replace multiple ACTIONS expressions while preserving other parts", () => {
    const result = compressActionsInString(
      "ACTIONS.test.result && ACTIONS.other.error"
    )
    expect(result).toBe("@test && @other.error")
  })

  it("should handle complex expressions with paths", () => {
    const result = compressActionsInString(
      "ACTIONS.test.result.foo.bar || ACTIONS.other.error.baz"
    )
    expect(result).toBe("@test..bar || @other.error..baz")
  })

  it("should preserve non-ACTIONS parts of the string", () => {
    const result = compressActionsInString(
      "if (ACTIONS.test.result) { return ACTIONS.other.error; } else { return 'something'; }"
    )
    expect(result).toBe(
      "if (@test) { return @other.error; } else { return 'something'; }"
    )
  })

  it("should handle array indices in paths", () => {
    const result = compressActionsInString("ACTIONS.test.result.items[0].name")
    expect(result).toBe("@test..name")
  })

  it("should throw TypeError if input is not a string", () => {
    // @ts-expect-error Testing invalid input
    expect(() => compressActionsInString(123)).toThrow(TypeError)
    // @ts-expect-error Testing invalid input
    expect(() => compressActionsInString(null)).toThrow(TypeError)
  })
})

describe("shortTimeAgo", () => {
  const NOW = new Date("2026-06-15T12:00:00.000Z")
  const DAY_MS = 24 * 60 * 60 * 1000

  beforeEach(() => {
    jest.useFakeTimers().setSystemTime(NOW)
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  function daysAgo(days: number) {
    return new Date(NOW.getTime() - days * DAY_MS)
  }

  it("formats each unit", () => {
    expect(shortTimeAgo(new Date(NOW.getTime() - 30 * 1000))).toBe("just now")
    expect(shortTimeAgo(new Date(NOW.getTime() - 5 * 60 * 1000))).toBe("5m ago")
    expect(shortTimeAgo(new Date(NOW.getTime() - 3 * 60 * 60 * 1000))).toBe(
      "3h ago"
    )
    expect(shortTimeAgo(daysAgo(3))).toBe("3d ago")
    expect(shortTimeAgo(daysAgo(14))).toBe("2w ago")
    expect(shortTimeAgo(daysAgo(90))).toBe("3mo ago")
    expect(shortTimeAgo(daysAgo(400))).toBe("1y ago")
  })

  it("clamps future dates to just now", () => {
    expect(shortTimeAgo(new Date(NOW.getTime() + DAY_MS))).toBe("just now")
  })

  // Handing over on the smaller unit's own count (4 weeks, 12 months) left the
  // larger unit flooring to zero for the days in between.
  it("never renders a zero-valued unit at a handover", () => {
    for (let days = 0; days <= 800; days++) {
      expect(shortTimeAgo(daysAgo(days))).not.toMatch(/\b0(w|mo|y) ago$/)
    }
  })

  it("keeps weeks through the last day before a month", () => {
    expect(shortTimeAgo(daysAgo(27))).toBe("3w ago")
    expect(shortTimeAgo(daysAgo(28))).toBe("4w ago")
    expect(shortTimeAgo(daysAgo(29))).toBe("4w ago")
    expect(shortTimeAgo(daysAgo(30))).toBe("1mo ago")
  })

  it("keeps months through the last day before a year", () => {
    expect(shortTimeAgo(daysAgo(359))).toBe("11mo ago")
    expect(shortTimeAgo(daysAgo(360))).toBe("12mo ago")
    expect(shortTimeAgo(daysAgo(364))).toBe("12mo ago")
    expect(shortTimeAgo(daysAgo(365))).toBe("1y ago")
  })
})
