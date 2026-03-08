import { getTokensCacheKey } from "@/components/ai-elements/code-block-cache"

describe("getTokensCacheKey", () => {
  it("does not collide for snippets that only differ in the middle", () => {
    const prefix = "import x from 'pkg'\n".repeat(8)
    const suffix = "\nexport default value\n".repeat(8)

    const middleA = "A".repeat(200)
    const middleB = "B".repeat(200)

    const codeA = `${prefix}${middleA}${suffix}`
    const codeB = `${prefix}${middleB}${suffix}`

    expect(codeA.length).toBe(codeB.length)

    const keyA = getTokensCacheKey(codeA, "javascript")
    const keyB = getTokensCacheKey(codeB, "javascript")

    expect(keyA).not.toBe(keyB)
  })
})
