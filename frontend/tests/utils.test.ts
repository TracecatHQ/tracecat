import {
  getDistributionData,
  getSlugFromActionKey,
  groupBy,
  isServer,
  nsTree,
  parseActionRunId,
  slugify,
  traverseNsTree,
  undoSlugify,
  undoSlugifyNamespaced,
} from "@/lib/utils"

describe("getDistributionData", () => {
  it("should calculate the distribution of values in an array of objects based on a specified key", () => {
    const data = [
      { fruit: "apple" },
      { fruit: "banana" },
      { fruit: "banana" },
      { fruit: "orange" },
      { fruit: "banana" },
      { fruit: "apple" },
    ]
    const distribution = getDistributionData(data, "fruit")
    expect(distribution).toEqual({
      apple: 2,
      banana: 3,
      orange: 1,
    })
  })
})

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

describe("constructNamespaceTree", () => {
  it("Simple", () => {
    const namespaces = [
      "namespace1",
      "namespace1.namespace2",
      "namespace1.namespace3",
    ]
    const tree = nsTree(namespaces)
    expect(tree).toEqual({
      namespace1: {
        namespace2: {},
        namespace3: {},
      },
    })
  })

  it("More complex", () => {
    const namespaces = ["a", "b.c", "b.c.d", "b.c.e", "a.f", "b.g"]
    const tree = nsTree(namespaces)
    expect(tree).toEqual({
      a: {
        f: {},
      },
      b: {
        c: {
          d: {},
          e: {},
        },
        g: {},
      },
    })
    expect(traverseNsTree(tree)).toEqual(["a.f", "b.c.d", "b.c.e", "b.g"])
  })
})
