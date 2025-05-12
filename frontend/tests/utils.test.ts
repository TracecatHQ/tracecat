import {
  isServer,
  slugify,
  splitConditionalExpression,
  undoSlugify,
} from "@/lib/utils"

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

describe("splitConditionalExpression", () => {
  it("should return empty string for empty input", () => {
    const result = splitConditionalExpression("")
    expect(result).toBe("")
  })

  it("should return the input as is if no operators are present", () => {
    const result = splitConditionalExpression("ACTIONS.test.result")
    expect(result).toBe("ACTIONS.test.result")
  })

  it("should return the input as is if it's short enough", () => {
    const result = splitConditionalExpression("a && b", 10)
    expect(result).toBe("a && b")
  })

  it("should split expressions with AND operator when they exceed maxLength", () => {
    const result = splitConditionalExpression(
      "ACTIONS.test.result && ACTIONS.another.result",
      20
    )
    expect(result).toBe("ACTIONS.test.result\n&& ACTIONS.another.result")
  })

  it("should split expressions with OR operator when they exceed maxLength", () => {
    const result = splitConditionalExpression(
      "ACTIONS.test.result || ACTIONS.another.result",
      20
    )
    expect(result).toBe("ACTIONS.test.result\n|| ACTIONS.another.result")
  })

  it("should handle multiple operators and add line breaks at appropriate positions", () => {
    const result = splitConditionalExpression(
      "ACTIONS.a.result && ACTIONS.b.result || ACTIONS.c.result",
      15
    )
    expect(result).toBe(
      "ACTIONS.a.result\n&& ACTIONS.b.result\n|| ACTIONS.c.result"
    )
  })

  it("should keep short expressions on the same line even with operators", () => {
    const result = splitConditionalExpression("a && b || c", 30)
    expect(result).toBe("a && b || c")
  })

  it("should handle custom operators", () => {
    const result = splitConditionalExpression(
      "field1 > 10 AND field2 < 20",
      15,
      ["AND", ">", "<"]
    )
    expect(result).toBe("field1 > 10\nAND field2 < 20")
  })
})
