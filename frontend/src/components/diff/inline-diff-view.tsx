import Link from "next/link"
import { useMemo } from "react"
import { ProseDiff } from "@/components/diff/prose-diff"
import { UnifiedDiff } from "@/components/diff/unified-diff"
import { Button } from "@/components/ui/button"
import {
  computeProseDiff,
  computeUnifiedDiff,
  type DiffSegment,
  normalizeDiffInput,
  resolveDiffMode,
  type UnifiedDiffRow,
} from "@/lib/diff"
import { splitMarkdownFrontmatter } from "@/lib/markdown-frontmatter"
import { cn } from "@/lib/utils"

/** Props for {@link InlineDiffView}. */
export interface InlineDiffViewProps {
  path: string
  /** Current draft content. `null` when absent from the draft. */
  oldValue: string | null
  /** Selected historical content. `null` when absent from that version. */
  newValue: string | null
  mode?: "prose" | "unified"
  contentType?: string
  downloadUrl?: string | null
  className?: string
}

/**
 * Above this many characters on either side, skip diffing entirely and show
 * the too-large empty state: rendering hundreds of thousands of grid rows
 * inside a dialog is unusable regardless of diff cost.
 */
const MAX_RENDERABLE_DIFF_CHARS = 1_000_000

const NOT_PREVIEWABLE_COPY = "This file is not previewable inline."
const BOTH_ABSENT_COPY =
  "This file does not exist in the draft or in this version."
const ADDED_BY_VERSION_COPY = "Restoring this version adds this file."
const REMOVED_BY_VERSION_COPY = "Restoring this version removes this file."
const IDENTICAL_COPY =
  "No changes. Restoring this version leaves this file unchanged."
const TOO_LARGE_COPY = "This file is too large to compare."

/** Diff of one document section, after the prose-to-unified fallback. */
type ContentView =
  | { view: "prose"; segments: DiffSegment[] }
  | { view: "unified"; rows: UnifiedDiffRow[] }

type DiffViewModel =
  | { kind: "not-previewable" }
  | { kind: "both-absent" }
  | { kind: "identical" }
  | { kind: "too-large" }
  | { kind: "content"; content: ContentView; note?: string }
  | {
      kind: "markdown-sections"
      frontmatterRows: UnifiedDiffRow[]
      body: ContentView
    }

/**
 * Diff a document between the current draft (`oldValue`) and a selected
 * historical version (`newValue`), so highlighted text is what restoring that
 * version brings back and struck-through text is what the draft would lose.
 *
 * Picks prose or unified rendering from the explicit `mode` prop or the file
 * path, applies the prose-to-unified fallback for oversized or timed-out
 * prose diffs, splits markdown frontmatter into its own unified section, and
 * renders every empty state (absent sides, identical content, binary files,
 * oversized files).
 *
 * Deliberately theme-hook-free: no `useTheme`/`resolvedTheme` — that pattern
 * paints the light branch for one frame before hydration. The `--diff-*` CSS
 * tokens are defined in both `:root` and `.dark`, so first paint is correct
 * and the tree renders in jsdom with no provider.
 */
export function InlineDiffView({
  path,
  oldValue,
  newValue,
  mode,
  contentType,
  downloadUrl,
  className,
}: InlineDiffViewProps) {
  // Memoized on the two content strings: they come from React Query cache
  // objects, so their identities are stable across renders and the memo holds.
  const model = useMemo<DiffViewModel>(
    () =>
      buildViewModel({
        path,
        oldValue,
        newValue,
        mode,
        contentType,
        downloadUrl,
      }),
    [path, oldValue, newValue, mode, contentType, downloadUrl]
  )

  switch (model.kind) {
    case "not-previewable":
      return (
        <EmptyState className={className} copy={NOT_PREVIEWABLE_COPY}>
          {downloadUrl && <OpenFileButton downloadUrl={downloadUrl} />}
        </EmptyState>
      )
    case "both-absent":
      return <EmptyState className={className} copy={BOTH_ABSENT_COPY} />
    case "identical":
      return <EmptyState className={className} copy={IDENTICAL_COPY} />
    case "too-large":
      return (
        <EmptyState className={className} copy={TOO_LARGE_COPY}>
          {downloadUrl && <OpenFileButton downloadUrl={downloadUrl} />}
        </EmptyState>
      )
    case "markdown-sections":
      return (
        <div className={cn("flex min-w-0 flex-col gap-4", className)}>
          <section className="flex min-w-0 flex-col gap-2">
            <h3 className="text-xs font-medium text-muted-foreground">
              Frontmatter
            </h3>
            <UnifiedDiff rows={model.frontmatterRows} />
          </section>
          <section className="flex min-w-0 flex-col gap-2">
            <h3 className="text-xs font-medium text-muted-foreground">
              Content
            </h3>
            <ContentDiff content={model.body} />
          </section>
        </div>
      )
    case "content":
      return (
        <div className={cn("flex min-w-0 flex-col gap-3", className)}>
          {model.note && (
            <p className="text-xs text-muted-foreground">{model.note}</p>
          )}
          <ContentDiff content={model.content} />
        </div>
      )
  }
}

function buildViewModel({
  path,
  oldValue,
  newValue,
  mode,
  contentType,
  downloadUrl,
}: Omit<InlineDiffViewProps, "className">): DiffViewModel {
  if (oldValue === null && newValue === null) {
    // Binary and other non-inlineable files arrive with no text content on
    // either side and a download URL instead.
    if (downloadUrl) {
      return { kind: "not-previewable" }
    }
    return { kind: "both-absent" }
  }

  const oldText = normalizeDiffInput(oldValue ?? "")
  const newText = normalizeDiffInput(newValue ?? "")

  if (oldValue !== null && newValue !== null && oldText === newText) {
    return { kind: "identical" }
  }
  if (
    oldText.length > MAX_RENDERABLE_DIFF_CHARS ||
    newText.length > MAX_RENDERABLE_DIFF_CHARS
  ) {
    return { kind: "too-large" }
  }

  let note: string | undefined
  if (oldValue === null) {
    note = ADDED_BY_VERSION_COPY
  } else if (newValue === null) {
    note = REMOVED_BY_VERSION_COPY
  }

  const resolvedMode = mode ?? resolveDiffMode(path, contentType)
  if (resolvedMode === "unified") {
    return {
      kind: "content",
      note,
      content: {
        view: "unified",
        rows: computeUnifiedDiff(oldText, newText).rows,
      },
    }
  }

  if (isMarkdownPath(path)) {
    const oldSplit = splitMarkdownFrontmatter(oldText)
    const newSplit = splitMarkdownFrontmatter(newText)
    // Only split when both sides carry frontmatter — mixing a split side with
    // a whole-document side misattributes the `---` fences as content edits.
    if (oldSplit && newSplit) {
      return {
        kind: "markdown-sections",
        frontmatterRows: computeUnifiedDiff(
          oldSplit.frontmatter,
          newSplit.frontmatter
        ).rows,
        body: buildContentView(oldSplit.body, newSplit.body),
      }
    }
  }

  return { kind: "content", note, content: buildContentView(oldText, newText) }
}

/**
 * Compute a prose diff, falling back to a unified diff when prose diffing is
 * skipped for size or timeout.
 */
function buildContentView(oldText: string, newText: string): ContentView {
  const prose = computeProseDiff(oldText, newText)
  if (prose.status === "ok") {
    return { view: "prose", segments: prose.segments }
  }
  return { view: "unified", rows: computeUnifiedDiff(oldText, newText).rows }
}

function isMarkdownPath(path: string): boolean {
  const lower = path.toLowerCase()
  return lower.endsWith(".md") || lower.endsWith(".markdown")
}

function ContentDiff({ content }: { content: ContentView }) {
  if (content.view === "prose") {
    return <ProseDiff segments={content.segments} />
  }
  return <UnifiedDiff rows={content.rows} />
}

function EmptyState({
  copy,
  className,
  children,
}: {
  copy: string
  className?: string
  children?: React.ReactNode
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 px-4 py-8 text-center",
        className
      )}
    >
      <p className="text-sm text-muted-foreground">{copy}</p>
      {children}
    </div>
  )
}

function OpenFileButton({ downloadUrl }: { downloadUrl: string }) {
  return (
    <Button asChild size="sm" variant="outline">
      <Link href={downloadUrl} target="_blank" rel="noreferrer">
        Open file
      </Link>
    </Button>
  )
}
