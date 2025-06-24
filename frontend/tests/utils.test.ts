import { compressActionsInString } from "@/lib/expressions"
import { isServer, slugify, undoSlugify } from "@/lib/utils"

describe("slugify", () => {
  it("should convert a string to a slug", () => {
    const slug = slugify("Hello World")
    expect(slug).toBe("hello_world")
  })
})

describe("undoSlugify", () => {
  it("should convert a slug back to a string", () => {
    const string = undoSlugify("hello_world")
    expect(string).toBe("Hello World")
  })
})

describe("isServer", () => {
  it("should return true if the code is running on the server", () => {
    const result = isServer()
    expect(result).toBe(true)
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
