import type { Node as ProseMirrorNode } from "@tiptap/pm/model"
import { TableMap } from "@tiptap/pm/tables"

/**
 * Narrowest weight a column can be given, expressed in characters.
 *
 * Columns whose longest cell is shorter than this are treated as if they held
 * this many characters, so a column of empty or one-word cells still gets a
 * usable share of the table.
 */
export const MIN_COLUMN_WEIGHT_CHARS = 6

/**
 * Widest weight a column can be given, expressed in characters.
 *
 * Together with {@link MIN_COLUMN_WEIGHT_CHARS} this caps the spread between
 * the widest and narrowest column at roughly 6.7:1, so a column holding a
 * SHA-256 hash cannot starve its neighbours.
 */
export const MAX_COLUMN_WEIGHT_CHARS = 40

/**
 * Whether the table node carries author-provided column widths.
 *
 * `colwidth` is seeded with zeroes for colspan cells by prosemirror-tables, so
 * `[0, 300]` is a legitimate "second column is 300px wide" value and a bare
 * `[0]` means "no width". Only a positive entry counts as explicit.
 */
export function tableNodeHasExplicitWidths(table: ProseMirrorNode): boolean {
  for (let rowIndex = 0; rowIndex < table.childCount; rowIndex += 1) {
    const row = table.child(rowIndex)
    for (let cellIndex = 0; cellIndex < row.childCount; cellIndex += 1) {
      if (hasPositiveColwidth(row.child(cellIndex))) {
        return true
      }
    }
  }
  return false
}

/**
 * Turn per-column character weights into percentages that sum to exactly 100.
 *
 * Each weight is clamped to `[MIN_COLUMN_WEIGHT_CHARS, MAX_COLUMN_WEIGHT_CHARS]`
 * before it is normalised, and the last entry absorbs the rounding remainder so
 * the returned percentages never drift below or above a full row.
 */
export function columnPercentagesFromWeights(
  weights: readonly number[]
): number[] | null {
  if (weights.length === 0) {
    return null
  }

  const clamped = weights.map(clampColumnWeight)
  const total = clamped.reduce((sum, weight) => sum + weight, 0)
  if (total <= 0) {
    return null
  }

  const percentages = clamped.map((weight) =>
    roundToTwoDecimals((weight / total) * 100)
  )
  const lastIndex = percentages.length - 1
  const leading = percentages
    .slice(0, lastIndex)
    .reduce((sum, percentage) => sum + percentage, 0)
  percentages[lastIndex] = roundToTwoDecimals(100 - leading)

  return percentages
}

/**
 * A single derivation pass: the weights that were measured and the percentages
 * they normalise to.
 *
 * The two travel together so that a caller holding on to a derivation can
 * compare a later table against the weights that actually produced the widths
 * it is currently showing.
 */
export type DerivedColumnWidths = {
  /** Clamped per-column character weights, one per column. */
  weights: number[]
  /** Percentages applied to the `<col>` elements; sums to exactly 100. */
  percentages: number[]
}

/**
 * Derive a table's per-column character weights, clamped to
 * `[MIN_COLUMN_WEIGHT_CHARS, MAX_COLUMN_WEIGHT_CHARS]`.
 *
 * Weights come from the longest cell text in each column (header row included),
 * which is the only signal available for tables produced by an automation or an
 * agent. Clamping here rather than leaving it to normalisation is what makes
 * two weight lists comparable: equal weights derive equal percentages. Returns
 * `null` when the table is malformed and nothing can be measured.
 */
export function deriveColumnWeights(table: ProseMirrorNode): number[] | null {
  const weights = measureColumnWeights(table)
  if (!weights) {
    return null
  }
  return weights.map(clampColumnWeight)
}

/**
 * Derive proportional column widths for a table that carries no width metadata.
 *
 * Returns `null` when the table is malformed and no sensible proportions can be
 * read, in which case callers should leave the columns alone —
 * `table-layout: fixed` already renders them equally.
 */
export function deriveColumnWidths(
  table: ProseMirrorNode
): DerivedColumnWidths | null {
  const weights = deriveColumnWeights(table)
  if (!weights) {
    return null
  }
  // Re-clamping already clamped weights is a no-op, so these percentages are
  // exactly the ones these weights describe.
  const percentages = columnPercentagesFromWeights(weights)
  if (!percentages) {
    return null
  }
  return { weights, percentages }
}

/**
 * Whether two weight lists hold the same weights in a different order — the
 * fingerprint of a column move.
 *
 * A permutation leaves the multiset of weights untouched, and only a move can
 * produce one: editing the text of a single column changes exactly one weight,
 * which changes the multiset unless the edit was a no-op. So typing can never
 * be mistaken for a move, and a move — which only ever permutes the columns —
 * is always caught. Identical lists are not a reordering; nothing moved.
 */
export function columnWeightsAreReordered(
  weights: readonly number[],
  previous: readonly number[]
): boolean {
  if (weights.length !== previous.length) {
    return false
  }
  if (weights.every((weight, index) => weight === previous[index])) {
    return false
  }
  const sorted = [...weights].sort(compareWeights)
  const previousSorted = [...previous].sort(compareWeights)
  return sorted.every((weight, index) => weight === previousSorted[index])
}

/**
 * Derive proportional widths for a table and write them onto its `<col>`
 * elements, returning the derivation that was applied.
 *
 * Returns `null` — leaving the table to upstream — when it carries explicit
 * widths, and `null` after clearing any previously written width when no
 * proportions can be derived.
 */
export function applyDerivedColumnWidths(
  table: ProseMirrorNode,
  colgroup: HTMLElement,
  tableEl: HTMLTableElement
): DerivedColumnWidths | null {
  if (tableNodeHasExplicitWidths(table)) {
    return null
  }

  const derived = deriveColumnWidths(table)
  const percentages = derived?.percentages ?? null
  if (!writeColumnPercentages(colgroup, tableEl, percentages)) {
    return null
  }
  return derived
}

/**
 * Write an already derived set of percentage widths onto a rendered table.
 *
 * This is the re-apply half of {@link applyDerivedColumnWidths}: it performs no
 * measurement, so callers holding percentages from an earlier pass can restore
 * them without the column proportions shifting. Returns `false` — and applies
 * nothing — when the table has explicit widths or when the percentages no
 * longer match the rendered column count.
 */
export function applyColumnPercentages(
  table: ProseMirrorNode,
  colgroup: HTMLElement,
  tableEl: HTMLTableElement,
  percentages: readonly number[] | null
): boolean {
  if (tableNodeHasExplicitWidths(table)) {
    return false
  }
  return writeColumnPercentages(colgroup, tableEl, percentages)
}

function writeColumnPercentages(
  colgroup: HTMLElement,
  tableEl: HTMLTableElement,
  percentages: readonly number[] | null
): boolean {
  const cols = Array.from(colgroup.querySelectorAll("col"))

  if (!percentages || percentages.length !== cols.length) {
    // Nothing to apply, but a previous pass may have left percentages behind.
    // Upstream sets no `width` on width-less tables, so clearing is safe.
    for (const col of cols) {
      col.style.removeProperty("width")
    }
    return false
  }

  cols.forEach((col, index) => {
    col.style.width = `${percentages[index]}%`
    // Upstream writes `min-width: <cellMinWidth>px` on width-less columns and
    // never clears it, which would otherwise fight the percentages.
    col.style.removeProperty("min-width")
  })

  // Let the stylesheet's `width: 100%` govern the table box.
  tableEl.style.removeProperty("width")
  tableEl.style.removeProperty("min-width")

  return true
}

function readTableMap(table: ProseMirrorNode): TableMap | null {
  try {
    // Throws for tables that are still malformed, e.g. before `fixTables` runs.
    return TableMap.get(table)
  } catch {
    return null
  }
}

function measureColumnWeights(table: ProseMirrorNode): number[] | null {
  const map = readTableMap(table)
  if (!map) {
    return null
  }

  const { width, height } = map
  if (width < 1 || height < 1) {
    return null
  }

  const weights = new Array<number>(width).fill(0)
  for (let row = 0; row < height; row += 1) {
    // A cell spanning several columns appears at several map indices; cache its
    // share so its text is only measured once per row.
    const shareByCellPos = new Map<number, number>()
    for (let col = 0; col < width; col += 1) {
      const cellPos = map.map[row * width + col]
      let share = shareByCellPos.get(cellPos)
      if (share === undefined) {
        const cell = table.nodeAt(cellPos)
        if (!cell) {
          continue
        }
        share = cell.textContent.length / readColspan(cell)
        shareByCellPos.set(cellPos, share)
      }
      if (share > weights[col]) {
        weights[col] = share
      }
    }
  }

  return weights
}

function hasPositiveColwidth(cell: ProseMirrorNode): boolean {
  const colwidth: unknown = cell.attrs.colwidth
  if (!Array.isArray(colwidth)) {
    return false
  }
  return colwidth.some((width) => typeof width === "number" && width > 0)
}

function readColspan(cell: ProseMirrorNode): number {
  const colspan: unknown = cell.attrs.colspan
  if (typeof colspan === "number" && colspan > 0) {
    return colspan
  }
  return 1
}

function clampColumnWeight(weight: number): number {
  if (!Number.isFinite(weight)) {
    return MIN_COLUMN_WEIGHT_CHARS
  }
  return Math.min(
    Math.max(weight, MIN_COLUMN_WEIGHT_CHARS),
    MAX_COLUMN_WEIGHT_CHARS
  )
}

function roundToTwoDecimals(value: number): number {
  return Math.round(value * 100) / 100
}

function compareWeights(a: number, b: number): number {
  return a - b
}
