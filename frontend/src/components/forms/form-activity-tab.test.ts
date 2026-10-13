import type { FormFieldBlock } from "@/client"
import {
  formatFieldAnswer,
  getFieldBlocks,
  orderAnswers,
} from "@/components/forms/form-activity-tab"

describe("orderAnswers", () => {
  it("follows the form's field order, then the inputs with no field", () => {
    // Stored order is JSONB's, not the form's.
    const answers = {
      quote: { id: "file-1" },
      cost_center: "IT-0000",
      model: "mbp14",
      requester: { user_id: "user-1" },
      reason: "replacement",
    }
    const labels = {
      model: "Choose your laptop",
      reason: "Reason for request",
      quote: "Supporting document",
    }

    expect(orderAnswers(answers, labels).map(([name]) => name)).toEqual([
      "model",
      "reason",
      "quote",
      "cost_center",
      "requester",
    ])
  })

  it("skips fields the submission has no answer for", () => {
    expect(
      orderAnswers({ reason: "upgrade" }, { model: "Model", reason: "Reason" })
    ).toEqual([["reason", "upgrade"]])
  })
})

function field(overrides: Partial<FormFieldBlock>): FormFieldBlock {
  return {
    type: "field",
    name: "model",
    label: "Choose your laptop",
    widget: "text",
    required: true,
    multiple: false,
    ...overrides,
  }
}

describe("formatFieldAnswer", () => {
  const cards = field({
    widget: "cards",
    options: [
      { value: "mba", label: "MacBook Air" },
      { value: "mbp14", label: "MacBook Pro 14" },
    ],
  })

  it("shows an option by its label", () => {
    expect(formatFieldAnswer("mbp14", cards)).toBe("MacBook Pro 14")
  })

  it("shows each picked option of a multiple field by its label", () => {
    expect(formatFieldAnswer(["mba", "mbp14"], cards)).toBe(
      "MacBook Air\nMacBook Pro 14"
    )
  })

  it("keeps a value that is no longer an option", () => {
    expect(formatFieldAnswer("mbp16", cards)).toBe("mbp16")
  })

  it("shows a date field's value as a date, whatever the time zone", () => {
    const date = field({ name: "needed_by", widget: "date" })

    expect(formatFieldAnswer("2026-10-22T00:00:00", date)).toBe("Oct 22, 2026")
    expect(formatFieldAnswer("2026-10-22", date)).toBe("Oct 22, 2026")
  })

  it("shows a datetime field's value with its time", () => {
    const datetime = field({ name: "starts_at", widget: "datetime" })

    expect(formatFieldAnswer("2026-10-22T09:30:00", datetime)).toBe(
      "Oct 22, 2026 9:30 AM"
    )
  })

  it("leaves text that only looks like a date alone", () => {
    expect(formatFieldAnswer("2026-10-22", field({ widget: "text" }))).toBe(
      "2026-10-22"
    )
  })

  it("falls back for empty answers and inputs with no field", () => {
    expect(formatFieldAnswer(null, cards)).toBe("-")
    expect(formatFieldAnswer([], cards)).toBe("-")
    expect(formatFieldAnswer({}, field({ widget: "user" }))).toBe("-")
    expect(formatFieldAnswer("IT-0000", undefined)).toBe("IT-0000")
    expect(formatFieldAnswer(true, field({ widget: "toggle" }))).toBe("Yes")
  })
})

describe("getFieldBlocks", () => {
  it("indexes the form's fields by input name and skips other blocks", () => {
    const model = field({})

    expect(
      getFieldBlocks({ blocks: [{ type: "heading", text: "Delivery" }, model] })
    ).toEqual({ model })
  })
})
