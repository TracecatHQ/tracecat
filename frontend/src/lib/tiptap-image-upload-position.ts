import type { Transaction } from "@tiptap/pm/state"

/** Map a pending image insertion point through an editor transaction. */
export function mapImageUploadPosition(
  position: number,
  transaction: Transaction,
  association: -1 | 1 = 1
): number {
  return transaction.mapping.map(position, association)
}

/** Return whether an intervening transaction edits inside a pending selection. */
export function transactionTouchesImageReplacement(
  from: number,
  to: number,
  transaction: Transaction
): boolean {
  let mappedFrom = from
  let mappedTo = to
  for (const stepMap of transaction.mapping.maps) {
    let touchesRange = false
    stepMap.forEach((oldStart, oldEnd) => {
      if (oldStart === oldEnd) {
        touchesRange ||= oldStart > mappedFrom && oldStart < mappedTo
      } else {
        touchesRange ||= oldStart < mappedTo && oldEnd > mappedFrom
      }
    })
    if (touchesRange) {
      return true
    }
    mappedFrom = stepMap.map(mappedFrom, 1)
    mappedTo = stepMap.map(mappedTo, -1)
  }
  return false
}

/** Replace characters that can break TipTap's unescaped Markdown image alt. */
export function sanitizeMarkdownImageAlt(filename: string): string {
  return filename.replace(/[\\[\]\r\n]/g, "_")
}
