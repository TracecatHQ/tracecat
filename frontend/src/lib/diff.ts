import { diffLines, diffWords, diffWordsWithSpace } from "diff"

/** Kind of change carried by a single inline diff segment. */
export type DiffSegmentKind = "unchanged" | "added" | "removed"

/** A contiguous run of text sharing one change kind. */
export interface DiffSegment {
  kind: DiffSegmentKind
  value: string
}

/** Rendering mode for a diffed document. */
export type DiffMode = "prose" | "unified"

/** Kind of row in a unified (single column) diff. */
export type UnifiedRowKind = "unchanged" | "added" | "removed" | "gap"

/** One rendered row of a unified diff. */
export interface UnifiedDiffRow {
  /** Stable, unique key safe to use as a React `key`. */
  key: string
  kind: UnifiedRowKind
  /** Line number in `oldValue`. `null` on `added` and `gap` rows. */
  oldLineNumber: number | null
  /** Line number in `newValue`. `null` on `removed` and `gap` rows. */
  newLineNumber: number | null
  /** Row content. Always empty on `gap` rows. */
  segments: DiffSegment[]
  /** Number of unchanged lines collapsed by a `gap` row. */
  hiddenLineCount?: number
}

/** A paragraph of prose segments, split on blank lines. */
export interface ProseParagraph {
  /** Stable, unique key safe to use as a React `key`. */
  key: string
  segments: DiffSegment[]
}

/** Why prose diffing was skipped and the caller should render a unified diff. */
export type ProseDiffFallbackReason = "too-large" | "timeout"

/** Successful prose diff. */
export interface ProseDiffOk {
  status: "ok"
  segments: DiffSegment[]
  /** `false` when both sides are identical after normalization. */
  hasChanges: boolean
}

/** Prose diffing was skipped; the caller must fall back to a unified diff. */
export interface ProseDiffFallback {
  status: "fallback"
  reason: ProseDiffFallbackReason
}

/**
 * Result of {@link computeProseDiff}. Discriminated on `status` so callers must
 * handle the fallback-to-unified case explicitly.
 */
export type ProseDiffResult = ProseDiffOk | ProseDiffFallback

/** Result of {@link computeUnifiedDiff}. Unified diffing never falls back. */
export interface UnifiedDiffResult {
  rows: UnifiedDiffRow[]
  /** `false` when both sides are identical after normalization. */
  hasChanges: boolean
}

/**
 * Maximum input size, in characters, for prose diffing. `diffWords` cost grows
 * super-linearly with document size and edit distance; above this either side
 * is diffed as a unified diff instead. Measured against either side rather than
 * the combined length because runtime tracks the larger of the two documents.
 */
export const MAX_PROSE_DIFF_CHARS = 20_000

/** Abort budget handed to `diffWords`; jsdiff returns `undefined` on abort. */
export const PROSE_DIFF_TIMEOUT_MS = 250

const DEFAULT_CONTEXT_LINES = 3

/**
 * Normalize text before diffing: collapse CRLF and lone CR to LF, then strip
 * trailing newlines.
 *
 * Deliberately not `trimEnd()`: that also eats trailing spaces and tabs, which
 * are meaningful indentation in YAML and Python and show up as phantom
 * whitespace-only changes when only one side is trimmed.
 */
export function normalizeDiffInput(value: string): string {
  return value.replace(/\r\n/g, "\n").replace(/\r/g, "\n").replace(/\n+$/, "")
}

/**
 * Pick the rendering mode for a document from its path and optional content
 * type. Markdown and plain text render as prose; everything else renders as a
 * unified diff.
 *
 * `isMarkdownPath` in `src/lib/skills-studio.tsx` answers a similar question,
 * but that module is a `.tsx` pulling in zod, lucide and `Badge`. Three lines of
 * duplication keeps this module dependency-light.
 */
export function resolveDiffMode(path: string, contentType?: string): DiffMode {
  if (contentType) {
    // Tolerate parameters such as `text/markdown; charset=utf-8`.
    const mediaType = contentType.split(";")[0].trim().toLowerCase()
    if (mediaType === "text/markdown") {
      return "prose"
    }
  }
  const fileName = path.toLowerCase().split("/").pop() ?? ""
  const dotIndex = fileName.lastIndexOf(".")
  const extension = dotIndex === -1 ? "" : fileName.slice(dotIndex + 1)
  if (extension === "md" || extension === "markdown" || extension === "txt") {
    return "prose"
  }
  return "unified"
}

/**
 * Diff two prose documents word by word.
 *
 * `oldValue` is the current draft and `newValue` is the selected historical
 * version, so text present only in `newValue` is `added` and text present only
 * in `oldValue` is `removed`.
 *
 * Uses `diffWords` rather than `diffWordsWithSpace`: jsdiff v8's `WordDiff` runs
 * a `postProcess` step that pulls leading and trailing whitespace out of
 * added/removed chunks, which keeps highlight spans from having ragged
 * whitespace edges and stops a reflowed paragraph rendering as confetti of
 * one-space insertions. Accepted trade-off: a pure whitespace change is
 * invisible in prose mode.
 */
export function computeProseDiff(
  oldValue: string,
  newValue: string
): ProseDiffResult {
  const oldText = normalizeDiffInput(oldValue)
  const newText = normalizeDiffInput(newValue)

  if (oldText === newText) {
    return {
      status: "ok",
      segments:
        oldText.length === 0 ? [] : [{ kind: "unchanged", value: oldText }],
      hasChanges: false,
    }
  }

  if (
    oldText.length > MAX_PROSE_DIFF_CHARS ||
    newText.length > MAX_PROSE_DIFF_CHARS
  ) {
    return { status: "fallback", reason: "too-large" }
  }

  const changes = diffWords(oldText, newText, {
    timeout: PROSE_DIFF_TIMEOUT_MS,
  })
  if (!changes) {
    return { status: "fallback", reason: "timeout" }
  }

  const segments: DiffSegment[] = []
  for (const change of changes) {
    if (change.value.length === 0) {
      continue
    }
    segments.push({ kind: resolveChangeKind(change), value: change.value })
  }
  return { status: "ok", segments, hasChanges: true }
}

/**
 * Split a flat segment list into paragraphs on blank lines, i.e. runs of two or
 * more newlines. A segment straddling a paragraph boundary is split across the
 * resulting paragraphs, preserving its kind.
 */
export function splitSegmentsIntoParagraphs(
  segments: DiffSegment[]
): ProseParagraph[] {
  const paragraphs: DiffSegment[][] = []
  let current: DiffSegment[] = []

  for (const segment of segments) {
    const parts = segment.value.split(/\n{2,}/)
    for (let index = 0; index < parts.length; index++) {
      if (index > 0) {
        paragraphs.push(current)
        current = []
      }
      const part = parts[index]
      if (part.length > 0) {
        current.push({ kind: segment.kind, value: part })
      }
    }
  }
  paragraphs.push(current)

  return paragraphs
    .filter((paragraph) => paragraph.length > 0)
    .map((paragraph, index) => ({
      key: `paragraph-${index}`,
      segments: paragraph,
    }))
}

/**
 * Diff two documents line by line into a single column of unified rows.
 *
 * `oldValue` is the current draft and `newValue` is the selected historical
 * version, so lines present only in `newValue` are `added` and lines present
 * only in `oldValue` are `removed`.
 *
 * Runs of unchanged lines longer than `2 * contextLines` collapse into one
 * `gap` row carrying `hiddenLineCount`.
 */
export function computeUnifiedDiff(
  oldValue: string,
  newValue: string,
  contextLines: number = DEFAULT_CONTEXT_LINES
): UnifiedDiffResult {
  const oldText = normalizeDiffInput(oldValue)
  const newText = normalizeDiffInput(newValue)

  if (oldText === newText) {
    return { rows: buildUnchangedRows(oldText), hasChanges: false }
  }

  const rows: UnifiedDiffRow[] = []
  let oldLineNumber = 0
  let newLineNumber = 0

  // `ignoreNewlineAtEof` makes a final line compare equal to the same line
  // followed by a newline. Without it, appending a line to a document reports
  // the previous last line as changed, because its token gained a newline.
  const changes = diffLines(oldText, newText, { ignoreNewlineAtEof: true })
  for (const change of changes) {
    const kind = resolveChangeKind(change)
    for (const line of splitLines(change.value)) {
      if (kind === "added") {
        newLineNumber += 1
        rows.push(buildRow("added", null, newLineNumber, line))
      } else if (kind === "removed") {
        oldLineNumber += 1
        rows.push(buildRow("removed", oldLineNumber, null, line))
      } else {
        oldLineNumber += 1
        newLineNumber += 1
        rows.push(buildRow("unchanged", oldLineNumber, newLineNumber, line))
      }
    }
  }

  // Prose diffing falls back here once either document exceeds its safe word
  // diff bound. Keep that fallback bounded by skipping the same quadratic
  // intra-line pass; large inputs still receive line-level additions and
  // removals from `diffLines`.
  if (
    oldText.length <= MAX_PROSE_DIFF_CHARS &&
    newText.length <= MAX_PROSE_DIFF_CHARS
  ) {
    applyWordLevelHighlights(rows)
  }

  return {
    rows: collapseUnchangedRuns(rows, contextLines),
    hasChanges: rows.some((row) => row.kind !== "unchanged"),
  }
}

/** Map a jsdiff change object onto a segment kind. */
function resolveChangeKind(change: {
  added: boolean
  removed: boolean
}): DiffSegmentKind {
  if (change.added) {
    return "added"
  }
  if (change.removed) {
    return "removed"
  }
  return "unchanged"
}

/**
 * Split a jsdiff line-chunk value into lines. The chunk ends with a newline for
 * every line it covers except at the very end of the document, and inputs are
 * normalized to have no trailing newline, so a trailing empty entry is an
 * artifact of the split rather than a real blank line.
 */
function splitLines(value: string): string[] {
  const lines = value.split("\n")
  if (lines.length > 1 && lines[lines.length - 1] === "") {
    lines.pop()
  }
  return lines
}

function buildRow(
  kind: Exclude<UnifiedRowKind, "gap">,
  oldLineNumber: number | null,
  newLineNumber: number | null,
  line: string
): UnifiedDiffRow {
  return {
    key: `${kind}-${oldLineNumber ?? "x"}-${newLineNumber ?? "x"}`,
    kind,
    oldLineNumber,
    newLineNumber,
    segments: [{ kind, value: line }],
  }
}

/** Rows for an unchanged document, used by the identical-input short circuit. */
function buildUnchangedRows(text: string): UnifiedDiffRow[] {
  if (text.length === 0) {
    return []
  }
  return text
    .split("\n")
    .map((line, index) => buildRow("unchanged", index + 1, index + 1, line))
}

/**
 * Pair each run of removed rows with the immediately following run of added
 * rows and compute intra-line highlights for the overlapping prefix.
 *
 * Uses `diffWordsWithSpace` because indentation is semantic in YAML and Python.
 * Rows beyond the overlap keep their whole-line segments.
 */
function applyWordLevelHighlights(rows: UnifiedDiffRow[]): void {
  let index = 0
  while (index < rows.length) {
    if (rows[index].kind !== "removed") {
      index += 1
      continue
    }
    const removedStart = index
    while (index < rows.length && rows[index].kind === "removed") {
      index += 1
    }
    const addedStart = index
    while (index < rows.length && rows[index].kind === "added") {
      index += 1
    }
    const removedCount = addedStart - removedStart
    const addedCount = index - addedStart
    const pairCount = Math.min(removedCount, addedCount)
    for (let offset = 0; offset < pairCount; offset++) {
      highlightPair(rows[removedStart + offset], rows[addedStart + offset])
    }
  }
}

/** Rewrite a removed/added row pair with word-level segments. */
function highlightPair(
  removedRow: UnifiedDiffRow,
  addedRow: UnifiedDiffRow
): void {
  const removedLine = removedRow.segments[0]?.value ?? ""
  const addedLine = addedRow.segments[0]?.value ?? ""
  if (!shouldHighlightPair(removedLine, addedLine)) {
    return
  }

  const changes = diffWordsWithSpace(removedLine, addedLine)
  const removedSegments: DiffSegment[] = []
  const addedSegments: DiffSegment[] = []
  for (const change of changes) {
    if (change.value.length === 0) {
      continue
    }
    const kind = resolveChangeKind(change)
    if (kind !== "added") {
      removedSegments.push({ kind, value: change.value })
    }
    if (kind !== "removed") {
      addedSegments.push({ kind, value: change.value })
    }
  }
  if (removedSegments.length === 0 || addedSegments.length === 0) {
    return
  }
  removedRow.segments = removedSegments
  addedRow.segments = addedSegments
}

/**
 * Heuristic guarding word-level highlighting: two lines are only worth pairing
 * when neither is blank and they share at least one whitespace-delimited token.
 * Unrelated lines otherwise render as word confetti.
 */
function shouldHighlightPair(removedLine: string, addedLine: string): boolean {
  const removedTokens = tokenize(removedLine)
  const addedTokens = tokenize(addedLine)
  if (removedTokens.length === 0 || addedTokens.length === 0) {
    return false
  }
  const addedTokenSet = new Set(addedTokens)
  return removedTokens.some((token) => addedTokenSet.has(token))
}

function tokenize(line: string): string[] {
  const trimmed = line.trim()
  if (trimmed.length === 0) {
    return []
  }
  return trimmed.split(/\s+/)
}

/**
 * Replace runs of more than `2 * contextLines` unchanged rows with a single gap
 * row, keeping `contextLines` rows of context on each side of the run.
 */
function collapseUnchangedRuns(
  rows: UnifiedDiffRow[],
  contextLines: number
): UnifiedDiffRow[] {
  const collapsed: UnifiedDiffRow[] = []
  let index = 0
  while (index < rows.length) {
    if (rows[index].kind !== "unchanged") {
      collapsed.push(rows[index])
      index += 1
      continue
    }
    const runStart = index
    while (index < rows.length && rows[index].kind === "unchanged") {
      index += 1
    }
    const run = rows.slice(runStart, index)
    if (run.length <= contextLines * 2) {
      for (const row of run) {
        collapsed.push(row)
      }
      continue
    }
    const leading = run.slice(0, contextLines)
    const trailing = run.slice(run.length - contextLines)
    const hidden = run[contextLines]
    for (const row of leading) {
      collapsed.push(row)
    }
    collapsed.push({
      key: `gap-${hidden.oldLineNumber ?? "x"}-${hidden.newLineNumber ?? "x"}`,
      kind: "gap",
      oldLineNumber: null,
      newLineNumber: null,
      segments: [],
      hiddenLineCount: run.length - contextLines * 2,
    })
    for (const row of trailing) {
      collapsed.push(row)
    }
  }
  return collapsed
}
