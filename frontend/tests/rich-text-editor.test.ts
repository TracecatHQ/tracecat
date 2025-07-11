import type { Block } from "@blocknote/core"

import { getSpacedBlocks } from "@/lib/rich-text-editor"

describe("getSpacedBlocks", () => {
  const createParagraphBlock = (): Block => ({
    type: "paragraph",
    content: [],
    id: "test-id",
    props: {
      textAlignment: "left",
      backgroundColor: "default",
      textColor: "default",
    },
    children: [],
  })

  const createHeadingBlock = (level: 1 | 2 | 3): Block => ({
    type: "heading",
    content: [],
    id: "test-id",
    props: {
      level,
      textAlignment: "left",
      backgroundColor: "default",
      textColor: "default",
    },
    children: [],
  })

  it("should not modify a single block", () => {
    const singleBlock = [createParagraphBlock()]
    const result = getSpacedBlocks(singleBlock)
    expect(result).toEqual(singleBlock)
  })

  it("should add spacing before each heading level 1 block", () => {
    const blocks = [
      createParagraphBlock(),
      createHeadingBlock(1),
      createParagraphBlock(),
      createHeadingBlock(1),
    ]

    const result = getSpacedBlocks(blocks)
    expect(result).toHaveLength(6) // Original 4 blocks + 2 spacing blocks
    expect(result[0].type).toBe("paragraph")
    expect(result[1].type).toBe("paragraph") // First spacing block
    expect(result[2].type).toBe("heading")
    expect(result[3].type).toBe("paragraph")
    expect(result[4].type).toBe("paragraph") // Second spacing block
    expect(result[5].type).toBe("heading")
  })

  it("should not add spacing before non-heading-1 blocks", () => {
    const blocks = [
      createParagraphBlock(),
      createHeadingBlock(2),
      createParagraphBlock(),
      createHeadingBlock(3),
    ]

    const result = getSpacedBlocks(blocks)
    expect(result).toHaveLength(4) // No spacing blocks added
    expect(result[0].type).toBe("paragraph")
    expect(result[1].type).toBe("heading")
    expect(result[2].type).toBe("paragraph")
    expect(result[3].type).toBe("heading")
  })

  it("should respect custom predicate for spacing", () => {
    const customPredicate = (block: Block) => block.type === "paragraph"
    const blocks = [
      createHeadingBlock(1),
      createParagraphBlock(),
      createHeadingBlock(2),
      createParagraphBlock(),
    ]

    const result = getSpacedBlocks(blocks, { predicate: customPredicate })
    expect(result).toHaveLength(6) // Original 4 blocks + 2 spacing blocks
    expect(result[0].type).toBe("heading")
    expect(result[1].type).toBe("paragraph") // Spacing block
    expect(result[2].type).toBe("paragraph")
    expect(result[3].type).toBe("heading")
    expect(result[4].type).toBe("paragraph") // Spacing block
    expect(result[5].type).toBe("paragraph")
  })

  it("should handle empty blocks array", () => {
    const result = getSpacedBlocks([])
    expect(result).toEqual([])
  })

  it("should preserve block properties in spacing blocks", () => {
    const blocks = [createParagraphBlock(), createHeadingBlock(1)]
    const result = getSpacedBlocks(blocks)

    const spacingBlock = result[1]
    expect(spacingBlock.props).toEqual({
      textAlignment: "left",
      backgroundColor: "default",
      textColor: "default",
    })
  })
})
