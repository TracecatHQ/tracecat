import { orderAnswers } from "@/components/forms/form-activity-tab"

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
