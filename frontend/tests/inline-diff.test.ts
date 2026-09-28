import type { Change } from "diff"
import {
  computeProseDiff,
  computeUnifiedDiff,
  type DiffSegment,
  MAX_PROSE_DIFF_CHARS,
  normalizeDiffInput,
  PROSE_DIFF_TIMEOUT_MS,
  resolveDiffMode,
  splitSegmentsIntoParagraphs,
  type UnifiedDiffRow,
} from "@/lib/diff"

const actualDiff = jest.requireActual<typeof import("diff")>("diff")

const mockDiffWords = jest.fn(
  (
    oldStr: string,
    newStr: string,
    options: { timeout: number }
  ): Change[] | undefined => actualDiff.diffWords(oldStr, newStr, options)
)
const mockDiffWordsWithSpace = jest.fn(
  (
    oldStr: string,
    newStr: string,
    options: { timeout: number }
  ): Change[] | undefined =>
    actualDiff.diffWordsWithSpace(oldStr, newStr, options)
)

jest.mock("diff", () => {
  const actual = jest.requireActual<typeof import("diff")>("diff")
  return {
    __esModule: true,
    ...actual,
    diffWords: (oldStr: string, newStr: string, options: { timeout: number }) =>
      mockDiffWords(oldStr, newStr, options),
    diffWordsWithSpace: (
      oldStr: string,
      newStr: string,
      options: { timeout: number }
    ) => mockDiffWordsWithSpace(oldStr, newStr, options),
  }
})

beforeEach(() => {
  jest.clearAllMocks()
})

function segmentFor(segments: DiffSegment[], needle: string): DiffSegment {
  const match = segments.find((segment) => segment.value.includes(needle))
  if (!match) {
    throw new Error(`no segment containing "${needle}"`)
  }
  return match
}

function contentOf(row: UnifiedDiffRow): string {
  return row.segments.map((segment) => segment.value).join("")
}

describe("normalizeDiffInput", () => {
  it("collapses CRLF and lone CR to LF", () => {
    expect(normalizeDiffInput("a\r\nb\rc")).toBe("a\nb\nc")
  })

  it("strips trailing newlines only, preserving trailing spaces", () => {
    expect(normalizeDiffInput("a\nb  \n\n")).toBe("a\nb  ")
  })

  it("preserves leading and interior indentation", () => {
    expect(normalizeDiffInput("  key:\n    value\r\n")).toBe(
      "  key:\n    value"
    )
  })

  it("returns an empty string for newline-only input", () => {
    expect(normalizeDiffInput("\r\n\n")).toBe("")
  })
})

describe("resolveDiffMode", () => {
  it("treats markdown and text extensions as prose", () => {
    expect(resolveDiffMode("instructions.md")).toBe("prose")
    expect(resolveDiffMode("docs/readme.markdown")).toBe("prose")
    expect(resolveDiffMode("notes.txt")).toBe("prose")
    expect(resolveDiffMode("NOTES.MD")).toBe("prose")
  })

  it("defaults unknown and code extensions to unified", () => {
    expect(resolveDiffMode("config.yaml")).toBe("unified")
    expect(resolveDiffMode("main.py")).toBe("unified")
    expect(resolveDiffMode("LICENSE")).toBe("unified")
    expect(resolveDiffMode("archive.md.gz")).toBe("unified")
  })

  it("treats a text/markdown content type as prose", () => {
    expect(resolveDiffMode("blob", "text/markdown")).toBe("prose")
    expect(resolveDiffMode("blob", "TEXT/Markdown; charset=utf-8")).toBe(
      "prose"
    )
  })

  it("ignores content types that are not markdown", () => {
    expect(resolveDiffMode("blob", "application/yaml")).toBe("unified")
    expect(resolveDiffMode("blob", "text/plain")).toBe("unified")
  })

  it("still honors the extension when the content type is unrelated", () => {
    expect(resolveDiffMode("notes.md", "application/octet-stream")).toBe(
      "prose"
    )
  })
})

describe("computeProseDiff", () => {
  it("marks text only in newValue as added and text only in oldValue as removed", () => {
    const result = computeProseDiff("the quick brown fox", "the slow brown fox")
    if (result.status !== "ok") {
      throw new Error("expected an ok prose diff")
    }
    expect(result.hasChanges).toBe(true)
    expect(segmentFor(result.segments, "quick").kind).toBe("removed")
    expect(segmentFor(result.segments, "slow").kind).toBe("added")
    expect(segmentFor(result.segments, "brown").kind).toBe("unchanged")
  })

  it("does not invert direction when text is only appended to the version", () => {
    const result = computeProseDiff("shared", "shared extra")
    if (result.status !== "ok") {
      throw new Error("expected an ok prose diff")
    }
    expect(segmentFor(result.segments, "extra").kind).toBe("added")
    expect(result.segments.some((segment) => segment.kind === "removed")).toBe(
      false
    )
  })

  it("short-circuits identical inputs after normalization", () => {
    const result = computeProseDiff("same text\r\n", "same text")
    if (result.status !== "ok") {
      throw new Error("expected an ok prose diff")
    }
    expect(result.hasChanges).toBe(false)
    expect(result.segments).toEqual([{ kind: "unchanged", value: "same text" }])
    expect(mockDiffWords).not.toHaveBeenCalled()
  })

  it("returns no segments when both sides are empty", () => {
    const result = computeProseDiff("", "\n")
    if (result.status !== "ok") {
      throw new Error("expected an ok prose diff")
    }
    expect(result.segments).toEqual([])
    expect(result.hasChanges).toBe(false)
  })

  it("passes the abort budget to diffWords", () => {
    computeProseDiff("alpha", "beta")
    expect(mockDiffWords).toHaveBeenCalledWith("alpha", "beta", {
      timeout: 250,
    })
  })

  it("falls back to unified when either side exceeds the size guard", () => {
    const large = "word ".repeat(MAX_PROSE_DIFF_CHARS / 4)
    expect(large.length).toBeGreaterThan(MAX_PROSE_DIFF_CHARS)
    expect(computeProseDiff("small", large)).toEqual({
      status: "fallback",
      reason: "too-large",
    })
    expect(computeProseDiff(large, "small")).toEqual({
      status: "fallback",
      reason: "too-large",
    })
    expect(mockDiffWords).not.toHaveBeenCalled()
  })

  it("falls back to unified when diffWords aborts", () => {
    mockDiffWords.mockReturnValueOnce(undefined)
    expect(computeProseDiff("alpha", "beta")).toEqual({
      status: "fallback",
      reason: "timeout",
    })
  })
})

describe("splitSegmentsIntoParagraphs", () => {
  it("splits on blank lines and keeps single newlines inside a paragraph", () => {
    const paragraphs = splitSegmentsIntoParagraphs([
      { kind: "unchanged", value: "first\nline\n\nsecond" },
    ])
    expect(paragraphs).toHaveLength(2)
    expect(paragraphs[0].segments).toEqual([
      { kind: "unchanged", value: "first\nline" },
    ])
    expect(paragraphs[1].segments).toEqual([
      { kind: "unchanged", value: "second" },
    ])
    expect(new Set(paragraphs.map((paragraph) => paragraph.key)).size).toBe(2)
  })

  it("splits a segment that straddles a paragraph boundary while preserving kind", () => {
    const paragraphs = splitSegmentsIntoParagraphs([
      { kind: "unchanged", value: "intro " },
      { kind: "added", value: "tail of one\n\nhead of two" },
      { kind: "unchanged", value: " outro" },
    ])
    expect(paragraphs).toHaveLength(2)
    expect(paragraphs[0].segments).toEqual([
      { kind: "unchanged", value: "intro " },
      { kind: "added", value: "tail of one" },
    ])
    expect(paragraphs[1].segments).toEqual([
      { kind: "added", value: "head of two" },
      { kind: "unchanged", value: " outro" },
    ])
  })

  it("drops empty paragraphs produced by leading or repeated blank lines", () => {
    const paragraphs = splitSegmentsIntoParagraphs([
      { kind: "removed", value: "\n\n\nonly\n\n\n" },
    ])
    expect(paragraphs).toHaveLength(1)
    expect(paragraphs[0].segments).toEqual([{ kind: "removed", value: "only" }])
  })

  it("returns no paragraphs for an empty segment list", () => {
    expect(splitSegmentsIntoParagraphs([])).toEqual([])
  })
})

describe("computeUnifiedDiff", () => {
  it("short-circuits identical inputs", () => {
    const result = computeUnifiedDiff("a\nb\r\n", "a\nb")
    expect(result.hasChanges).toBe(false)
    expect(result.rows.map((row) => row.kind)).toEqual([
      "unchanged",
      "unchanged",
    ])
    expect(result.rows[1].oldLineNumber).toBe(2)
    expect(result.rows[1].newLineNumber).toBe(2)
  })

  it("numbers rows with null on the correct side", () => {
    const result = computeUnifiedDiff(
      "alpha\ndraft only\nomega",
      "alpha\nomega"
    )
    expect(result.hasChanges).toBe(true)
    const removed = result.rows.filter((row) => row.kind === "removed")
    expect(removed).toHaveLength(1)
    expect(contentOf(removed[0])).toBe("draft only")
    expect(removed[0].oldLineNumber).toBe(2)
    expect(removed[0].newLineNumber).toBeNull()

    const unchanged = result.rows.filter((row) => row.kind === "unchanged")
    expect(unchanged.map((row) => row.oldLineNumber)).toEqual([1, 3])
    expect(unchanged.map((row) => row.newLineNumber)).toEqual([1, 2])
  })

  it("marks lines only present in the version as added", () => {
    const result = computeUnifiedDiff("alpha", "alpha\nversion only")
    const added = result.rows.filter((row) => row.kind === "added")
    expect(added).toHaveLength(1)
    expect(contentOf(added[0])).toBe("version only")
    expect(added[0].oldLineNumber).toBeNull()
    expect(added[0].newLineNumber).toBe(2)
  })

  it("emits only added rows when the draft side is empty", () => {
    const result = computeUnifiedDiff("", "alpha\nbeta")
    expect(result.rows.map((row) => row.kind)).toEqual(["added", "added"])
    expect(result.rows.map((row) => row.newLineNumber)).toEqual([1, 2])
  })

  it("gives every row a unique key", () => {
    const result = computeUnifiedDiff(
      "alpha\nbeta\ngamma",
      "alpha\ndelta\ngamma\nepsilon"
    )
    const keys = result.rows.map((row) => row.key)
    expect(new Set(keys).size).toBe(keys.length)
  })

  it("computes word level highlights inside a removed/added run", () => {
    const result = computeUnifiedDiff("key: alpha\ntail", "key: beta\ntail")
    const removed = result.rows.filter((row) => row.kind === "removed")[0]
    const added = result.rows.filter((row) => row.kind === "added")[0]
    expect(removed.segments).toEqual([
      { kind: "unchanged", value: "key: " },
      { kind: "removed", value: "alpha" },
    ])
    expect(added.segments).toEqual([
      { kind: "unchanged", value: "key: " },
      { kind: "added", value: "beta" },
    ])
    expect(mockDiffWordsWithSpace).toHaveBeenCalledWith(
      "key: alpha",
      "key: beta",
      { timeout: PROSE_DIFF_TIMEOUT_MS }
    )
  })

  it("keeps whole-line segments when word highlighting times out", () => {
    mockDiffWordsWithSpace.mockReturnValueOnce(undefined)
    const result = computeUnifiedDiff("shared alpha", "shared beta")
    const removed = result.rows.find((row) => row.kind === "removed")
    const added = result.rows.find((row) => row.kind === "added")
    expect(removed?.segments).toEqual([
      { kind: "removed", value: "shared alpha" },
    ])
    expect(added?.segments).toEqual([{ kind: "added", value: "shared beta" }])
  })

  it("keeps whitespace when highlighting, since indentation is semantic", () => {
    const result = computeUnifiedDiff("  key: alpha", "    key: alpha")
    const removed = result.rows.filter((row) => row.kind === "removed")[0]
    const added = result.rows.filter((row) => row.kind === "added")[0]
    expect(contentOf(removed)).toBe("  key: alpha")
    expect(contentOf(added)).toBe("    key: alpha")
    expect(added.segments.some((segment) => segment.kind === "added")).toBe(
      true
    )
  })

  it("skips word pairing when the lines share no token", () => {
    const result = computeUnifiedDiff("alpha beta\ntail", "gamma delta\ntail")
    const removed = result.rows.filter((row) => row.kind === "removed")[0]
    const added = result.rows.filter((row) => row.kind === "added")[0]
    expect(removed.segments).toEqual([{ kind: "removed", value: "alpha beta" }])
    expect(added.segments).toEqual([{ kind: "added", value: "gamma delta" }])
  })

  it("skips word pairing when one line is blank", () => {
    const result = computeUnifiedDiff("one\n   \nend", "one\nzzz\nend")
    const removed = result.rows.filter((row) => row.kind === "removed")[0]
    const added = result.rows.filter((row) => row.kind === "added")[0]
    expect(removed.segments).toEqual([{ kind: "removed", value: "   " }])
    expect(added.segments).toEqual([{ kind: "added", value: "zzz" }])
  })

  it("skips word highlights when a document exceeds the prose bound", () => {
    const longSharedLine = `shared ${"x".repeat(MAX_PROSE_DIFF_CHARS)}`
    const result = computeUnifiedDiff(
      `${longSharedLine} draft`,
      `${longSharedLine} version`
    )
    const removed = result.rows.find((row) => row.kind === "removed")
    const added = result.rows.find((row) => row.kind === "added")
    expect(removed?.segments).toEqual([
      { kind: "removed", value: `${longSharedLine} draft` },
    ])
    expect(added?.segments).toEqual([
      { kind: "added", value: `${longSharedLine} version` },
    ])
  })

  it("only pairs the overlapping prefix when the runs differ in length", () => {
    const result = computeUnifiedDiff(
      "alpha one\ntail",
      "alpha two\nalpha three\ntail"
    )
    const removed = result.rows.filter((row) => row.kind === "removed")
    const added = result.rows.filter((row) => row.kind === "added")
    expect(removed).toHaveLength(1)
    expect(added).toHaveLength(2)
    expect(removed[0].segments.length).toBeGreaterThan(1)
    expect(added[0].segments.length).toBeGreaterThan(1)
    expect(added[1].segments).toEqual([{ kind: "added", value: "alpha three" }])
  })

  it("collapses long unchanged runs into a gap row", () => {
    const context = Array.from({ length: 20 }, (_, index) => `line ${index}`)
    const result = computeUnifiedDiff(
      `${context.join("\n")}\nzzz draft`,
      `${context.join("\n")}\nzzz version`
    )
    const gaps = result.rows.filter((row) => row.kind === "gap")
    expect(gaps).toHaveLength(1)
    expect(gaps[0].hiddenLineCount).toBe(14)
    expect(gaps[0].oldLineNumber).toBeNull()
    expect(gaps[0].newLineNumber).toBeNull()
    expect(gaps[0].segments).toEqual([])
    expect(result.rows.map((row) => row.kind)).toEqual([
      "unchanged",
      "unchanged",
      "unchanged",
      "gap",
      "unchanged",
      "unchanged",
      "unchanged",
      "removed",
      "added",
    ])
  })

  it("honors a custom context size", () => {
    const context = Array.from({ length: 10 }, (_, index) => `line ${index}`)
    const result = computeUnifiedDiff(
      `${context.join("\n")}\nzzz draft`,
      `${context.join("\n")}\nzzz version`,
      1
    )
    const gaps = result.rows.filter((row) => row.kind === "gap")
    expect(gaps).toHaveLength(1)
    expect(gaps[0].hiddenLineCount).toBe(8)
  })

  it("keeps short unchanged runs intact", () => {
    const context = Array.from({ length: 6 }, (_, index) => `line ${index}`)
    const result = computeUnifiedDiff(
      `${context.join("\n")}\nzzz draft`,
      `${context.join("\n")}\nzzz version`
    )
    expect(result.rows.some((row) => row.kind === "gap")).toBe(false)
  })
})
