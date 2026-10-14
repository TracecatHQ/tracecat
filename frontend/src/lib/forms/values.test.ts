import { fieldBlock } from "@/lib/forms/test-fixtures"
import {
  type FormBlock,
  getInitialFormValues,
  getVisibleFields,
  toSubmissionValues,
  validateFormValues,
} from "@/lib/forms/values"

// The renderer tests cover hidden fields, required text and file uploads end
// to end. These cover the rules they do not reach.

describe("getInitialFormValues", () => {
  it("starts from defaults, but leaves user and file defaults to the server", () => {
    expect(
      getInitialFormValues([
        { type: "divider" },
        fieldBlock({ name: "reason", default: "New hire" }),
        fieldBlock({ name: "urgent", widget: "toggle", required: true }),
        fieldBlock({ name: "manager", widget: "user", default: { id: "x" } }),
        fieldBlock({ name: "quote", widget: "file", default: { id: "y" } }),
      ])
    ).toEqual({ reason: "New hire", urgent: false, manager: null, quote: [] })
  })

  it("sends nothing for an untouched toggle whose default is null", () => {
    // A `bool | None` input with a null default: the run must get null.
    const blocks: FormBlock[] = [
      fieldBlock({ name: "escalate", widget: "toggle", default: null }),
      fieldBlock({ name: "remote", widget: "toggle", default: false }),
    ]
    const values = getInitialFormValues(blocks)

    expect(values).toEqual({ escalate: null, remote: false })
    expect(toSubmissionValues(blocks, values)).toEqual({ remote: false })
  })
})

describe("getVisibleFields", () => {
  const visible = (blocks: FormBlock[], values: Record<string, unknown>) =>
    getVisibleFields(blocks, values).map((field) => field.name)

  // An empty answer is not sent, so the server applies the controller's
  // default. The client has to agree.
  it.each([
    ["the answer matches", undefined, "other", true],
    ["the answer differs from the default", "other", "laptop", false],
    ["an empty answer has a matching default", "other", "", true],
    ["an empty answer has another default", "laptop", null, false],
    ["an empty answer has no default", undefined, undefined, false],
  ])("follows the controller when %s", (_case, fallback, kind, shown) => {
    const blocks: FormBlock[] = [
      fieldBlock({ name: "kind", widget: "select", default: fallback }),
      fieldBlock({
        name: "details",
        visible_if: { field: "kind", equals: "other" },
      }),
    ]
    expect(visible(blocks, { kind }).includes("details")).toBe(shown)
  })

  it("compares strictly and hides a field whose controller is missing", () => {
    const blocks: FormBlock[] = [
      fieldBlock({ name: "count", widget: "number" }),
      fieldBlock({ name: "why", visible_if: { field: "count", equals: 1 } }),
      fieldBlock({ name: "lost", visible_if: { field: "gone", equals: 1 } }),
    ]
    expect(visible(blocks, { count: 1, gone: 1 })).toEqual(["count", "why"])
    expect(visible(blocks, { count: "1" })).toEqual(["count"])
    expect(visible(blocks, { count: true })).toEqual(["count"])
  })
})

describe("validateFormValues", () => {
  it("counts false and 0 as answers and waits for uploads", () => {
    const blocks: FormBlock[] = [
      fieldBlock({ name: "agree", widget: "toggle", required: true }),
      fieldBlock({ name: "count", widget: "number", required: true }),
      fieldBlock({ name: "extras", required: true, multiple: true }),
      fieldBlock({ name: "quote", widget: "file" }),
    ]
    const quote = [
      { key: "1", name: "quote.pdf", size: 10, status: "uploading" },
    ]
    expect(
      validateFormValues(blocks, { agree: false, count: 0, extras: [], quote })
    ).toEqual({
      extras: "This field is required",
      quote: "Wait for the uploads to finish",
    })
  })
})

describe("validateFormValues whole numbers", () => {
  it("refuses one a browser cannot hold exactly", () => {
    const blocks: FormBlock[] = [
      fieldBlock({ name: "account", widget: "number" }),
      fieldBlock({ name: "largest", widget: "number" }),
      fieldBlock({ name: "ratio", widget: "number" }),
    ]
    // Typed as 9007199254740993, the value is already rounded in state.
    const typed = Number("9007199254740993")

    expect(
      validateFormValues(blocks, {
        account: typed,
        largest: Number.MAX_SAFE_INTEGER,
        ratio: 0.5,
      })
    ).toEqual({
      account:
        "Enter a whole number between -9,007,199,254,740,991 and 9,007,199,254,740,991",
    })
  })
})

describe("toSubmissionValues", () => {
  it("sends user IDs for user fields", () => {
    const blocks: FormBlock[] = [
      fieldBlock({ name: "manager", widget: "user" }),
      fieldBlock({ name: "approvers", widget: "user", multiple: true }),
    ]
    expect(
      toSubmissionValues(blocks, {
        manager: { user_id: "user-1", email: "maya.okafor@example.com" },
        approvers: [{ user_id: "user-2" }, { user_id: "user-3" }],
      })
    ).toEqual({ manager: "user-1", approvers: ["user-2", "user-3"] })
  })
})
