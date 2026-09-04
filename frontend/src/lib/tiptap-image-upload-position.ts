import type { Transaction } from "@tiptap/pm/state"

/** Map a pending image insertion point through an editor transaction. */
export function mapImageUploadPosition(
  position: number,
  transaction: Transaction
): number {
  return transaction.mapping.map(position, 1)
}
