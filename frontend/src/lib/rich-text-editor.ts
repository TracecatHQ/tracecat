import type { Block } from "@blocknote/core"
import { v4 as uuidv4 } from "uuid"

interface SpacingOptions {
  /**
   * The predicate to use to determine if a block should be spaced.
   * We insert spaces above headings with level 1.
   * @default (block) => block.type === "heading" && block.props.level === 1
   */
  predicate?: (block: Block) => boolean
}

const defaultPredicate = (block: Block): boolean =>
  block.type === "heading" && block.props.level === 1

/**
 * Adds spacing blocks before certain blocks in a BlockNote document based on a predicate.
 * By default, adds empty paragraph blocks before heading level 1 blocks.
 *
 * @param blocks - Array of BlockNote Block objects to process
 * @param options - Optional configuration object
 * @param options.predicate - Custom predicate function to determine which blocks need spacing
 * @returns Array of Block objects with spacing blocks inserted where needed
 *
 * @example
 * ```ts
 * // Add spacing before h1 headings (default behavior)
 * const spacedBlocks = getSpacedBlocks(blocks);
 *
 * // Custom spacing for paragraphs
 * const customSpaced = getSpacedBlocks(blocks, {
 *   predicate: (block) => block.type === "paragraph"
 * });
 * ```
 */
export function getSpacedBlocks(
  blocks: Block[],
  options?: SpacingOptions
): Block[] {
  // First block is always unaffected
  const spacedBlocks: Block[] = [blocks[0]]
  const predicate = options?.predicate || defaultPredicate
  // Iterate over the next block
  for (let i = 1; i < blocks.length; i++) {
    const curr = blocks[i]
    if (predicate(curr)) {
      spacedBlocks.push({
        type: "paragraph",
        content: [],
        id: uuidv4(),
        props: {
          textAlignment: "left",
          backgroundColor: "default",
          textColor: "default",
        },
        children: [],
      })
    }
    spacedBlocks.push(curr)
  }
  return spacedBlocks
}
