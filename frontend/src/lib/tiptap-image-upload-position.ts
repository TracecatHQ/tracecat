import type { Transaction } from "@tiptap/pm/state"

/** Map a pending image insertion point through an editor transaction. */
export function mapImageUploadPosition(
  position: number,
  transaction: Transaction,
  association: -1 | 1 = 1
): number {
  return transaction.mapping.map(position, association)
}
