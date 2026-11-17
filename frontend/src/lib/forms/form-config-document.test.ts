import {
  formBlockKind,
  insertFormConfigItem,
  moveFormConfigItem,
  readFormVisualConfig,
  removeFormConfigItem,
  setFormConfigValue,
} from "@/lib/forms/form-config-document"

const TEXT = `# Laptop form
title: Laptop request
layout:
  - field: model # keep me
    widget: cards
  - heading: Details
`

describe("readFormVisualConfig", () => {
  it("reads the settings and layout", () => {
    expect(readFormVisualConfig(TEXT)).toEqual({
      title: "Laptop request",
      description: "",
      successMessage: "",
      layout: [{ field: "model", widget: "cards" }, { heading: "Details" }],
    })
  })

  it("reads empty text as an empty config", () => {
    expect(readFormVisualConfig("")?.layout).toEqual([])
    expect(readFormVisualConfig("layout:\n")?.layout).toEqual([])
  })

  it("gives up on syntax errors and shapes it can't show", () => {
    expect(readFormVisualConfig("title: [broken\n")).toBeNull()
    expect(readFormVisualConfig("- a\n")).toBeNull()
    expect(readFormVisualConfig("layout: model\n")).toBeNull()
    expect(readFormVisualConfig("layout:\n  - model\n")).toBeNull()
  })
})

describe("formBlockKind", () => {
  it("names the block by its content key", () => {
    expect(formBlockKind({ field: "a", label: "A" })).toBe("field")
    expect(formBlockKind({ notice: "N", tone: "info" })).toBe("notice")
    expect(formBlockKind({ label: "A" })).toBeNull()
  })
})

describe("setFormConfigValue", () => {
  it("sets a value and keeps comments and other keys", () => {
    const next = setFormConfigValue(TEXT, ["layout", 0, "label"], "Model")
    expect(next).toContain("# Laptop form")
    expect(next).toContain("# keep me")
    expect(readFormVisualConfig(next)?.layout[0]).toEqual({
      field: "model",
      widget: "cards",
      label: "Model",
    })
  })

  it("keeps strings that look like other types as strings", () => {
    const next = setFormConfigValue(TEXT, ["title"], "true")
    expect(readFormVisualConfig(next)?.title).toBe("true")
  })

  it("removes empty values unless told to keep them", () => {
    const removed = setFormConfigValue(TEXT, ["title"], "")
    expect(removed).not.toContain("title:")
    expect(setFormConfigValue(TEXT, ["description"], "")).toBe(TEXT)
    const kept = setFormConfigValue(TEXT, ["layout", 1, "heading"], "", {
      keepEmpty: true,
    })
    expect(readFormVisualConfig(kept)?.layout[1]).toEqual({ heading: "" })
  })

  it("sets a value in empty text", () => {
    expect(setFormConfigValue("", ["title"], "Order")).toBe("title: Order\n")
  })
})

describe("list edits", () => {
  it("adds a block, creating the layout when missing", () => {
    expect(
      insertFormConfigItem("title: A\n", ["layout"], { divider: true })
    ).toBe("title: A\nlayout:\n  - divider: true\n")
    const next = insertFormConfigItem(TEXT, ["layout"], {
      notice: "N",
      tone: "info",
    })
    expect(readFormVisualConfig(next)?.layout[2]).toEqual({
      notice: "N",
      tone: "info",
    })
  })

  it("moves a block with its comment", () => {
    const next = moveFormConfigItem(TEXT, ["layout"], 0, 1)
    expect(next).toBe(`# Laptop form
title: Laptop request
layout:
  - heading: Details
  - field: model # keep me
    widget: cards
`)
  })

  it("removes an item, and an emptied list when asked", () => {
    const withOption = insertFormConfigItem(TEXT, ["layout", 0, "options"], {
      value: "mac",
    })
    expect(readFormVisualConfig(withOption)?.layout[0].options).toEqual([
      { value: "mac" },
    ])
    const removed = removeFormConfigItem(
      withOption,
      ["layout", 0, "options"],
      0,
      { dropEmpty: true }
    )
    expect(removed).toBe(TEXT)
    expect(
      readFormVisualConfig(removeFormConfigItem(TEXT, ["layout"], 0))?.layout
    ).toEqual([{ heading: "Details" }])
  })

  it("leaves text that doesn't parse alone", () => {
    expect(setFormConfigValue("title: [x\n", ["title"], "y")).toBe(
      "title: [x\n"
    )
  })
})
