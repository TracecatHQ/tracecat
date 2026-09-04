import type { Transaction } from "@tiptap/pm/state"

/** Map a pending image insertion point through an editor transaction. */
export function mapImageUploadPosition(
  position: number,
  transaction: Transaction,
  association: -1 | 1 = 1
): number {
  return transaction.mapping.map(position, association)
}

/** Replace characters that can break TipTap's unescaped Markdown image alt. */
export function sanitizeMarkdownImageAlt(filename: string): string {
  return filename.replace(/[\\[\]\r\n]/g, "_")
}
