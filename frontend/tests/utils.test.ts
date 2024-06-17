import {
  getSlugFromActionKey,
  groupBy,
  isServer,
  parseActionRunId,
  slugify,
  undoSlugify,
  undoSlugifyNamespaced,
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

describe("undoSlugifyNamespaced", () => {
  it("should convert a namespaced slug back to a string", () => {
    const string = undoSlugifyNamespaced("namespace1.namespace2.hello_world")
    expect(string).toBe("Namespace1 Namespace2 Hello World")
  })
})

describe("getSlugFromActionKey", () => {
  it("should extract the action slug from an action key", () => {
    const slug = getSlugFromActionKey("actionId.actionSlug")
    expect(slug).toBe("actionSlug")
  })
})

describe("parseActionRunId", () => {
  it("should parse an action run ID and return the specified field", () => {
    const actionRunId = "ar:actionId.actionSlug:workflowRunId"
    const actionId = parseActionRunId(actionRunId, "actionId")
    const actionSlug = parseActionRunId(actionRunId, "actionSlug")
    const workflowRunId = parseActionRunId(actionRunId, "workflowRunId")
    expect(actionId).toBe("actionId")
    expect(actionSlug).toBe("actionSlug")
    expect(workflowRunId).toBe("workflowRunId")
  })

  it("should throw an error for an invalid field", () => {
    const actionRunId = "ar:actionId.actionSlug:workflowRunId"
    const invalidField = "invalid"
    expect(() => parseActionRunId(actionRunId, invalidField as any)).toThrow(
      new Error("Invalid field")
    )
  })
})

describe("groupBy", () => {
  it("should group an array of objects by a specified key", () => {
    const array = [
      { category: "fruit", name: "apple" },
      { category: "fruit", name: "banana" },
      { category: "vegetable", name: "carrot" },
      { category: "fruit", name: "orange" },
      { category: "vegetable", name: "broccoli" },
    ]
    const grouped = groupBy(array, "category")
    expect(grouped).toEqual({
      fruit: [
        { category: "fruit", name: "apple" },
        { category: "fruit", name: "banana" },
        { category: "fruit", name: "orange" },
      ],
      vegetable: [
        { category: "vegetable", name: "carrot" },
        { category: "vegetable", name: "broccoli" },
      ],
    })
  })
})

describe("isServer", () => {
  it("should return true if the code is running on the server", () => {
    const result = isServer()
    expect(result).toBe(true)
  })
})
