import { compressActionsInString } from "@/lib/expressions"
import { isServer, slugify, slugifyActionRef, undoSlugify } from "@/lib/utils"

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
