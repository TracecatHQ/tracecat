"use client"

import ReactDiffViewer, {
  DiffMethod,
  type ReactDiffViewerStylesOverride,
} from "react-diff-viewer-continued"

const PROMPT_DIFF_STYLES: ReactDiffViewerStylesOverride = {
  variables: {
    light: {
      diffViewerBackground: "hsl(var(--background))",
      diffViewerTitleBackground: "hsl(var(--muted) / 0.45)",
      diffViewerColor: "hsl(var(--foreground))",
      diffViewerTitleColor: "hsl(var(--muted-foreground))",
      diffViewerTitleBorderColor: "hsl(var(--border))",
      addedBackground: "hsl(142 55% 93%)",
      addedColor: "hsl(var(--foreground))",
      removedBackground: "hsl(0 75% 95%)",
      removedColor: "hsl(var(--foreground))",
      changedBackground: "hsl(var(--muted) / 0.6)",
      wordAddedBackground: "hsl(142 65% 84%)",
      wordRemovedBackground: "hsl(0 85% 87%)",
      addedGutterBackground: "hsl(142 52% 88%)",
      removedGutterBackground: "hsl(0 72% 90%)",
      gutterBackground: "hsl(var(--muted) / 0.3)",
      gutterBackgroundDark: "hsl(var(--muted) / 0.3)",
      highlightBackground: "hsl(var(--accent) / 0.16)",
      highlightGutterBackground: "hsl(var(--accent) / 0.2)",
      codeFoldGutterBackground: "hsl(var(--muted) / 0.3)",
      codeFoldBackground: "hsl(var(--muted) / 0.2)",
      emptyLineBackground: "hsl(var(--background))",
      gutterColor: "hsl(var(--muted-foreground))",
      addedGutterColor: "hsl(var(--foreground))",
      removedGutterColor: "hsl(var(--foreground))",
      codeFoldContentColor: "hsl(var(--muted-foreground))",
    },
  },
  diffAdded: {
    background: "hsl(142 55% 93%)",
  },
  diffRemoved: {
    background: "hsl(0 75% 95%)",
  },
  diffContainer: {
    border: 0,
    borderRadius: 0,
    overflow: "hidden",
    width: "100%",
    minWidth: 0,
    maxWidth: "100%",
    fontSize: "12px",
    lineHeight: 1.6,
  },
  titleBlock: {
    display: "flex",
    alignItems: "center",
    fontSize: "11px",
    fontWeight: 600,
    letterSpacing: "0.01em",
    textTransform: "none",
    padding: "0.625rem 0.75rem",
  },
  content: {
    width: "100%",
    maxWidth: "100%",
    fontFamily:
      "ui-monospace, SFMono-Regular, SF Mono, Menlo, Monaco, Consolas, Liberation Mono, monospace",
  },
  column: {
    minWidth: 0,
    borderColor: "hsl(var(--border))",
  },
  splitView: {
    "@media (max-width: 768px)": {
      gridTemplateColumns: "1fr",
    },
  },
  line: {
    minHeight: "1.8rem",
  },
  gutter: {
    minWidth: "3rem",
    borderColor: "hsl(var(--border))",
  },
  marker: {
    minWidth: "2rem",
    borderColor: "hsl(var(--border))",
  },
  lineNumber: {
    color: "hsl(var(--muted-foreground))",
  },
  contentText: {
    display: "block",
    maxWidth: "100%",
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
  },
  wordAdded: {
    background: "hsl(142 65% 84%)",
    borderRadius: "0.2rem",
    padding: "0.05rem 0",
  },
  wordRemoved: {
    background: "hsl(0 85% 87%)",
    borderRadius: "0.2rem",
    padding: "0.05rem 0",
  },
  codeFold: {
    background: "hsl(var(--muted) / 0.2)",
  },
  codeFoldContentContainer: {
    color: "hsl(var(--muted-foreground))",
  },
}

interface AgentPresetPromptDiffProps {
  baseLabel: string
  basePrompt: string | null | undefined
  compareLabel: string
  comparePrompt: string | null | undefined
}

export function AgentPresetPromptDiff({
  baseLabel,
  basePrompt,
  compareLabel,
  comparePrompt,
}: AgentPresetPromptDiffProps) {
  const normalizedBasePrompt = basePrompt?.trimEnd() ?? ""
  const normalizedComparePrompt = comparePrompt?.trimEnd() ?? ""

  if (normalizedBasePrompt === "" && normalizedComparePrompt === "") {
    return (
      <div className="rounded-md border px-3 py-6 text-xs text-muted-foreground">
        No instructions in either version.
      </div>
    )
  }

  return (
    <div className="min-w-0 overflow-x-auto rounded-md border bg-background">
      <ReactDiffViewer
        oldValue={normalizedBasePrompt || "No instructions"}
        newValue={normalizedComparePrompt || "No instructions"}
        leftTitle={baseLabel}
        rightTitle={compareLabel}
        splitView
        compareMethod={DiffMethod.WORDS_WITH_SPACE}
        showDiffOnly={false}
        hideSummary
        styles={PROMPT_DIFF_STYLES}
      />
    </div>
  )
}
