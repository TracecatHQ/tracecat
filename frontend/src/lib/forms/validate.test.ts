import { fieldBlock } from "@/lib/forms/test-fixtures"
import {
  getInitialFormValues,
  isIsoDate,
  validateFormValues,
} from "@/lib/forms/validate"
import type { FormBlock } from "@/lib/forms/visibility"

const OPTIONS = [
  { value: "mba", label: "MacBook Air" },
  { value: "mbp", label: "MacBook Pro" },
]

describe("getInitialFormValues", () => {
  it("uses each field's default, or its widget's empty value", () => {
    const blocks: FormBlock[] = [
      { type: "divider" },
      fieldBlock({ name: "title" }),
      fieldBlock({ name: "reason", default: "New hire" }),
      fieldBlock({ name: "count", widget: "number" }),
      fieldBlock({ name: "urgent", widget: "toggle" }),
      fieldBlock({ name: "ship_home", widget: "toggle", default: true }),
      fieldBlock({ name: "model", widget: "select", options: OPTIONS }),
      fieldBlock({ name: "extras", widget: "multi_select", multiple: true }),
      fieldBlock({ name: "manager", widget: "user", default: { id: "x" } }),
      fieldBlock({ name: "quote", widget: "file" }),
    ]
    expect(getInitialFormValues(blocks)).toEqual({
      title: "",
      reason: "New hire",
      count: null,
      urgent: false,
      ship_home: true,
      model: null,
      extras: [],
      manager: null,
      quote: [],
    })
  })
})

describe("isIsoDate", () => {
  it.each(["2026-10-26", "2024-02-29"])("accepts %s", (value) => {
    expect(isIsoDate(value)).toBe(true)
  })

  it.each([
    "2026-02-30",
    "2026-13-01",
    "26-10-2026",
    "2026-10-26T00:00:00Z",
    "",
    null,
    20261026,
  ])("rejects %p", (value) => {
    expect(isIsoDate(value)).toBe(false)
  })
})

describe("validateFormValues", () => {
  it("requires a value for required fields but accepts a false toggle", () => {
    const blocks: FormBlock[] = [
      fieldBlock({ name: "title", required: true }),
      fieldBlock({ name: "extras", required: true, multiple: true }),
      fieldBlock({ name: "manager", widget: "user", required: true }),
      fieldBlock({ name: "agree", widget: "toggle", required: true }),
      fieldBlock({ name: "notes" }),
    ]
    expect(
      validateFormValues(blocks, {
        title: "",
        extras: [],
        manager: null,
        agree: false,
        notes: "",
      })
    ).toEqual({
      title: "This field is required",
      extras: "This field is required",
      manager: "This field is required",
    })
  })

  it("checks option membership for single and list fields", () => {
    const blocks: FormBlock[] = [
      fieldBlock({ name: "model", widget: "select", options: OPTIONS }),
      fieldBlock({
        name: "models",
        widget: "cards",
        multiple: true,
        options: OPTIONS,
      }),
    ]
    expect(
      validateFormValues(blocks, { model: "mba", models: ["mbp"] })
    ).toEqual({})
    expect(
      validateFormValues(blocks, { model: "dell", models: ["mba", "dell"] })
    ).toEqual({
      model: "Choose one of the options",
      models: "Choose one of the options",
    })
  })

  it("accepts only finite numbers", () => {
    const blocks: FormBlock[] = [fieldBlock({ name: "n", widget: "number" })]
    expect(validateFormValues(blocks, { n: 0 })).toEqual({})
    expect(validateFormValues(blocks, { n: 1.5 })).toEqual({})
    expect(validateFormValues(blocks, { n: Number.NaN })).toEqual({
      n: "Enter a number",
    })
    expect(validateFormValues(blocks, { n: Number.POSITIVE_INFINITY })).toEqual(
      { n: "Enter a number" }
    )
  })

  it("checks dates and datetimes", () => {
    const blocks: FormBlock[] = [
      fieldBlock({ name: "day", widget: "date" }),
      fieldBlock({ name: "at", widget: "datetime" }),
    ]
    expect(
      validateFormValues(blocks, {
        day: "2026-10-26",
        at: "2026-10-26T09:30:00.000Z",
      })
    ).toEqual({})
    expect(
      validateFormValues(blocks, { day: "2026-02-30", at: "tomorrow" })
    ).toEqual({
      day: "Enter a valid date",
      at: "Enter a valid date and time",
    })
  })

  it("blocks submission while an upload is pending or has failed", () => {
    const blocks: FormBlock[] = [
      fieldBlock({ name: "quote", widget: "file", multiple: true }),
    ]
    const file = { key: "1", name: "quote.pdf", size: 10 }
    expect(
      validateFormValues(blocks, { quote: [{ ...file, status: "uploading" }] })
    ).toEqual({ quote: "Wait for the uploads to finish" })
    expect(
      validateFormValues(blocks, { quote: [{ ...file, status: "failed" }] })
    ).toEqual({ quote: "Remove the files that failed to upload" })
    expect(
      validateFormValues(blocks, { quote: [{ ...file, status: "selected" }] })
    ).toEqual({})
  })

  it("does not validate hidden fields", () => {
    const blocks: FormBlock[] = [
      fieldBlock({ name: "other", widget: "toggle", default: false }),
      fieldBlock({
        name: "recipient",
        required: true,
        visible_if: { field: "other", equals: true },
      }),
    ]
    expect(validateFormValues(blocks, { other: false, recipient: "" })).toEqual(
      {}
    )
    expect(validateFormValues(blocks, { other: true, recipient: "" })).toEqual({
      recipient: "This field is required",
    })
  })
})
