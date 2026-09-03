const codeToTokensMock = jest.fn(() => ({
  bg: "transparent",
  fg: "inherit",
  tokens: [[{ content: "x", color: "#fff" }]],
}))

jest.mock("shiki", () => ({
  createHighlighter: jest.fn(async () => ({
    getLoadedLanguages: () => ["javascript"],
    codeToTokens: codeToTokensMock,
  })),
}))

import { highlightCode } from "@/components/ai-elements/code-block"

async function flushMicrotasks() {
  await Promise.resolve()
  await Promise.resolve()
}

describe("highlightCode", () => {
  beforeEach(() => {
    codeToTokensMock.mockClear()
  })

  it("dedupes in-flight tokenization for identical inputs", async () => {
    const cb1 = jest.fn()
    const cb2 = jest.fn()

    expect(highlightCode("const a = 1", "javascript", cb1)).toBeNull()
    expect(highlightCode("const a = 1", "javascript", cb2)).toBeNull()

    await flushMicrotasks()

    expect(codeToTokensMock).toHaveBeenCalledTimes(1)
    expect(cb1).toHaveBeenCalledTimes(1)
    expect(cb2).toHaveBeenCalledTimes(1)
  })

  it("evicts old entries once cache exceeds max size", async () => {
    for (let idx = 0; idx <= 500; idx += 1) {
      highlightCode(`const value = ${idx}`, "javascript")
    }

    await flushMicrotasks()

    const beforeRecall = codeToTokensMock.mock.calls.length
    expect(highlightCode("const value = 0", "javascript")).toBeNull()
    await flushMicrotasks()

    expect(codeToTokensMock.mock.calls.length).toBeGreaterThan(beforeRecall)
  })
})
