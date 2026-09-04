import type { Transaction } from "@tiptap/pm/state"

/** Map a pending image insertion point through an editor transaction. */
export function mapImageUploadPosition(
  position: number,
  transaction: Transaction
): number {
  return transaction.mapping.map(position, 1)
}

/** Delete selected text before an image paste, returning a dispatchable edit. */
export function deleteImagePasteSelection(
  transaction: Transaction,
  from: number,
  to: number
): Transaction | null {
  return from === to ? null : transaction.delete(from, to)
}
