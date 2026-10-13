import { fieldBlock } from "@/lib/forms/test-fixtures"
import {
  type FormBlock,
  getVisibleFields,
  isEmptyFormValue,
  toSubmissionValues,
} from "@/lib/forms/visibility"

const blocks: FormBlock[] = [
  { type: "heading", text: "Delivery" },
  fieldBlock({ name: "for_someone_else", widget: "toggle", default: false }),
  fieldBlock({
    name: "recipient",
    widget: "user",
    visible_if: { field: "for_someone_else", equals: true },
  }),
  fieldBlock({ name: "notes" }),
]

function names(values: Record<string, unknown>): string[] {
  return getVisibleFields(blocks, values).map((field) => field.name)
}

describe("getVisibleFields", () => {
  it("returns unconditional fields in block order and skips other blocks", () => {
    expect(names({ for_someone_else: false })).toEqual([
      "for_someone_else",
      "notes",
    ])
  })

  it("shows a conditional field while its controller equals the rule", () => {
    expect(names({ for_someone_else: true })).toEqual([
      "for_someone_else",
      "recipient",
      "notes",
    ])
  })

  it("falls back to the controller's default when it has no value", () => {
    expect(names({})).not.toContain("recipient")
    const defaultOn = blocks.map((block) =>
      block.type === "field" && block.name === "for_someone_else"
        ? { ...block, default: true }
        : block
    )
    expect(
      getVisibleFields(defaultOn, {}).map((field) => field.name)
    ).toContain("recipient")
  })

  describe("when the requester clears the controller", () => {
    // A cleared answer is not submitted, so the server sees no value and
    // applies the controller's default. The client has to agree.
    function clearedController(fallback?: string): FormBlock[] {
      return [
        fieldBlock({ name: "kind", widget: "select", default: fallback }),
        fieldBlock({
          name: "details",
          default: "none",
          visible_if: { field: "kind", equals: "other" },
        }),
      ]
    }
    const cleared = ["", null, undefined]

    it.each(cleared)("shows the field when the default matches: %p", (kind) => {
      const form = clearedController("other")
      expect(getVisibleFields(form, { kind }).map((f) => f.name)).toEqual([
        "kind",
        "details",
      ])
      // The controller itself is left out, which is what triggers the default.
      expect(toSubmissionValues(form, { kind, details: "x" })).toEqual({
        details: "x",
      })
    })

    it.each(cleared)("hides it when the default differs: %p", (kind) => {
      const form = clearedController("laptop")
      expect(getVisibleFields(form, { kind }).map((f) => f.name)).toEqual([
        "kind",
      ])
      expect(toSubmissionValues(form, { kind, details: "x" })).toEqual({})
    })

    it.each(cleared)("hides it when there is no default: %p", (kind) => {
      const form = clearedController()
      expect(getVisibleFields(form, { kind }).map((f) => f.name)).toEqual([
        "kind",
      ])
    })

    it("still follows an answer that differs from the default", () => {
      const form = clearedController("other")
      expect(
        getVisibleFields(form, { kind: "laptop" }).map((f) => f.name)
      ).toEqual(["kind"])
    })
  })

  it("compares strictly, so a toggle never satisfies a number rule", () => {
    const numeric: FormBlock[] = [
      fieldBlock({ name: "count", widget: "number" }),
      fieldBlock({ name: "why", visible_if: { field: "count", equals: 1 } }),
    ]
    const visible = (count: unknown) =>
      getVisibleFields(numeric, { count }).map((field) => field.name)
    expect(visible(1)).toContain("why")
    expect(visible(true)).not.toContain("why")
    expect(visible("1")).not.toContain("why")
  })

  it("hides a field whose controller is not in the form", () => {
    const orphan: FormBlock[] = [
      fieldBlock({ name: "why", visible_if: { field: "missing", equals: 1 } }),
    ]
    expect(getVisibleFields(orphan, { missing: 1 })).toEqual([])
  })
})

describe("isEmptyFormValue", () => {
  it.each([undefined, null, "", []])("treats %p as empty", (value) => {
    expect(isEmptyFormValue(value)).toBe(true)
  })

  it.each([false, 0, " ", ["a"]])("treats %p as filled in", (value) => {
    expect(isEmptyFormValue(value)).toBe(false)
  })
})

describe("toSubmissionValues", () => {
  it("leaves out hidden fields and empty answers", () => {
    expect(
      toSubmissionValues(blocks, {
        for_someone_else: false,
        recipient: { user_id: "user-1", email: "maya.okafor@example.com" },
        notes: "",
      })
    ).toEqual({ for_someone_else: false })
  })

  it("sends user IDs for user fields", () => {
    const users: FormBlock[] = [
      fieldBlock({ name: "manager", widget: "user" }),
      fieldBlock({ name: "approvers", widget: "user", multiple: true }),
    ]
    expect(
      toSubmissionValues(users, {
        manager: { user_id: "user-1", email: "daniel.reyes@example.com" },
        approvers: [
          { user_id: "user-2", email: "a@example.com" },
          { user_id: "user-3", email: "b@example.com" },
        ],
      })
    ).toEqual({ manager: "user-1", approvers: ["user-2", "user-3"] })
  })

  it("sends only uploaded file IDs for file fields", () => {
    const files: FormBlock[] = [
      fieldBlock({ name: "quote", widget: "file" }),
      fieldBlock({ name: "photos", widget: "file", multiple: true }),
    ]
    expect(
      toSubmissionValues(files, {
        quote: [{ key: "1", name: "q.pdf", size: 1, status: "selected" }],
        photos: [
          {
            key: "2",
            name: "a.png",
            size: 1,
            status: "uploaded",
            fileId: "file-1",
          },
          { key: "3", name: "b.png", size: 1, status: "failed" },
        ],
      })
    ).toEqual({ photos: ["file-1"] })
  })
})
