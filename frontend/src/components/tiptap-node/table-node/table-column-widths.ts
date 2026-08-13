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
 * Derive proportional column widths for a table that carries no width metadata.
 *
 * Weights come from the longest cell text in each column (header row included),
 * which is the only signal available for tables produced by an automation or an
 * agent. Returns `null` when the table is malformed and no sensible proportions
 * can be read, in which case callers should leave the columns alone —
 * `table-layout: fixed` already renders them equally.
 */
export function deriveColumnPercentages(
  table: ProseMirrorNode
): number[] | null {
  const weights = deriveColumnWeights(table)
  if (!weights) {
    return null
  }
  return columnPercentagesFromWeights(weights)
}

/**
 * Number of columns in a table node, or `null` when it has none or is malformed.
 *
 * Callers use this to tell a structural change (a column was inserted or
 * deleted) apart from a content change, which must not move the columns.
 */
export function tableColumnCount(table: ProseMirrorNode): number | null {
  const map = readTableMap(table)
  if (!map || map.width < 1) {
    return null
  }
  return map.width
}

/**
 * Derive proportional widths for a table and write them onto its `<col>`
 * elements, returning the percentages that were applied.
 *
 * Returns `null` — leaving the table to upstream — when it carries explicit
 * widths, and `null` after clearing any previously written width when no
 * proportions can be derived.
 */
export function applyDerivedColumnWidths(
  table: ProseMirrorNode,
  colgroup: HTMLElement,
  tableEl: HTMLTableElement
): number[] | null {
  if (tableNodeHasExplicitWidths(table)) {
    return null
  }

  const percentages = deriveColumnPercentages(table)
  if (!writeColumnPercentages(colgroup, tableEl, percentages)) {
    return null
  }
  return percentages
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

function deriveColumnWeights(table: ProseMirrorNode): number[] | null {
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
