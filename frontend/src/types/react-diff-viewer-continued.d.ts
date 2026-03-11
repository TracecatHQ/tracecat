declare module "react-diff-viewer-continued" {
  import type { CSSProperties, JSX } from "react"

  export enum DiffMethod {
    CHARS = "diffChars",
    WORDS = "diffWords",
    WORDS_WITH_SPACE = "diffWordsWithSpace",
    LINES = "diffLines",
    TRIMMED_LINES = "diffTrimmedLines",
    SENTENCES = "diffSentences",
    CSS = "diffCss",
  }

  export type ReactDiffViewerStylesOverride = Record<string, unknown>

  export interface ReactDiffViewerProps {
    oldValue: string
    newValue: string
    splitView?: boolean
    compareMethod?: DiffMethod
    showDiffOnly?: boolean
    hideSummary?: boolean
    leftTitle?: string
    rightTitle?: string
    styles?: ReactDiffViewerStylesOverride
    className?: string
    lineNumberStyle?: CSSProperties
  }

  export default function ReactDiffViewer(
    props: ReactDiffViewerProps
  ): JSX.Element
}
