"use client"

import { CodeBlock, type CodeBlockOptions } from "@tiptap/extension-code-block"
import {
  type Editor,
  NodeViewContent,
  type NodeViewProps,
  NodeViewWrapper,
  ReactNodeViewRenderer,
} from "@tiptap/react"
import { useTheme } from "next-themes"
import * as React from "react"
import { cn } from "@/lib/utils"

type MermaidCodeBlockOptions = CodeBlockOptions & {
  renderWhenBlurred: boolean
}

type MermaidRenderState = {
  isEditable: boolean
  isFocused: boolean
  language: string
  renderWhenBlurred: boolean
}

const MERMAID_FONT_FAMILY =
  'var(--font-sans), ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'

/** Mermaid theme, resolved from the app's next-themes `resolvedTheme`. */
export type MermaidTheme = "light" | "dark"

type MermaidPalette = {
  text: string
  mutedText: string
  line: string
  border: string
  surface: string
  surfaceSubtle: string
  background: string
  accent: string
  accentSoft: string
  accentSofter: string
  activation: string
  arrowhead: string
  taskBkg: string
  taskBorder: string
  activeTaskBkg: string
  activeTaskBorder: string
  critBkg: string
  critBorder: string
  todayLine: string
  sankeyNode: string
  sankeyLink: string
  /**
   * Categorical ramp shared by section scales (`cScale*`), pie slices, and
   * git branches. Mermaid falls back to rainbow defaults for any index left
   * unset, so all twelve slots are defined.
   */
  categorical: readonly [
    string,
    string,
    string,
    string,
    string,
    string,
    string,
    string,
    string,
    string,
    string,
    string,
  ]
}

const MERMAID_LIGHT_PALETTE: MermaidPalette = {
  text: "#18181b",
  mutedText: "#71717a",
  line: "#71717a",
  border: "#d4d4d8",
  surface: "#f4f4f5",
  surfaceSubtle: "#fafafa",
  background: "#ffffff",
  accent: "#3b82f6",
  accentSoft: "#93c5fd",
  accentSofter: "#bfdbfe",
  activation: "#e4e4e7",
  arrowhead: "#a1a1aa",
  taskBkg: "#dbeafe",
  taskBorder: "#93c5fd",
  activeTaskBkg: "#93c5fd",
  activeTaskBorder: "#60a5fa",
  critBkg: "#fee2e2",
  critBorder: "#fca5a5",
  todayLine: "#f87171",
  sankeyNode: "#60a5fa",
  sankeyLink: "#bfdbfe",
  categorical: [
    "#dbeafe", // blue-100
    "#bfdbfe", // blue-200
    "#93c5fd", // blue-300
    "#c7d2fe", // indigo-200
    "#a5b4fc", // indigo-300
    "#bae6fd", // sky-200
    "#7dd3fc", // sky-300
    "#e0e7ff", // indigo-100
    "#e0f2fe", // sky-100
    "#ddd6fe", // violet-200
    "#c4b5fd", // violet-300
    "#e4e4e7", // zinc-200
  ],
}

const MERMAID_DARK_PALETTE: MermaidPalette = {
  text: "#fafafa",
  mutedText: "#a3a3a3",
  line: "#a3a3a3",
  border: "#404040",
  surface: "#262626",
  surfaceSubtle: "#171717",
  background: "#101010",
  accent: "#60a5fa",
  accentSoft: "#3b82f6",
  accentSofter: "#1d4ed8",
  activation: "#404040",
  arrowhead: "#a3a3a3",
  taskBkg: "#1e3a8a",
  taskBorder: "#3b82f6",
  activeTaskBkg: "#1d4ed8",
  activeTaskBorder: "#60a5fa",
  critBkg: "#7f1d1d",
  critBorder: "#ef4444",
  todayLine: "#f87171",
  sankeyNode: "#3b82f6",
  sankeyLink: "#1e3a8a",
  categorical: [
    "#1e3a8a", // blue-900
    "#1e40af", // blue-800
    "#1d4ed8", // blue-700
    "#312e81", // indigo-900
    "#3730a3", // indigo-800
    "#0c4a6e", // sky-900
    "#075985", // sky-800
    "#4338ca", // indigo-700
    "#0369a1", // sky-700
    "#4c1d95", // violet-900
    "#5b21b6", // violet-800
    "#404040", // neutral-700
  ],
}

/** Returns the Mermaid palette for a light or dark app theme. */
export function getMermaidPalette(theme: MermaidTheme): MermaidPalette {
  return theme === "dark" ? MERMAID_DARK_PALETTE : MERMAID_LIGHT_PALETTE
}

function getMermaidScaleVariables(palette: MermaidPalette) {
  const variables: Record<string, string> = {}

  palette.categorical.forEach((color, index) => {
    variables[`cScale${index}`] = color
    variables[`cScaleLabel${index}`] = palette.text
    variables[`pie${index + 1}`] = color

    if (index < 8) {
      variables[`git${index}`] = color
      variables[`gitBranchLabel${index}`] = palette.text
    }
  })

  return variables
}

/** Builds Mermaid `themeVariables` for the given app theme. */
export function getMermaidThemeVariables(theme: MermaidTheme) {
  const palette = getMermaidPalette(theme)

  return {
    darkMode: theme === "dark",
    fontFamily: MERMAID_FONT_FAMILY,
    fontSize: "12px",
    background: palette.background,
    mainBkg: palette.surface,
    secondBkg: palette.surfaceSubtle,
    tertiaryColor: palette.background,
    primaryColor: palette.surface,
    secondaryColor: palette.surfaceSubtle,
    primaryBorderColor: palette.border,
    secondaryBorderColor: palette.border,
    tertiaryBorderColor: palette.border,
    primaryTextColor: palette.text,
    secondaryTextColor: palette.text,
    tertiaryTextColor: palette.text,
    textColor: palette.text,
    titleColor: palette.text,
    darkTextColor: palette.text,
    lineColor: palette.line,
    arrowheadColor: palette.line,
    nodeBorder: palette.border,
    clusterBkg: palette.surfaceSubtle,
    clusterBorder: palette.border,
    defaultLinkColor: palette.line,
    edgeLabelBackground: palette.background,
    actorBkg: palette.surface,
    actorBorder: palette.border,
    actorTextColor: palette.text,
    actorLineColor: palette.line,
    signalColor: palette.line,
    signalTextColor: palette.text,
    labelBoxBkgColor: palette.surface,
    labelBoxBorderColor: palette.border,
    labelTextColor: palette.text,
    loopTextColor: palette.text,
    noteBkgColor: palette.surfaceSubtle,
    noteBorderColor: palette.border,
    noteTextColor: palette.text,
    activationBkgColor: palette.activation,
    activationBorderColor: palette.border,
    sectionBkgColor: palette.surface,
    altSectionBkgColor: palette.background,
    taskBkgColor: palette.taskBkg,
    taskTextColor: palette.text,
    taskTextOutsideColor: palette.text,
    taskBorderColor: palette.taskBorder,
    activeTaskBkgColor: palette.activeTaskBkg,
    activeTaskBorderColor: palette.activeTaskBorder,
    doneTaskBkgColor: palette.activation,
    doneTaskBorderColor: palette.border,
    critBkgColor: palette.critBkg,
    critBorderColor: palette.critBorder,
    todayLineColor: palette.todayLine,
    gridColor: palette.border,
    pieOpacity: "1",
    pieStrokeColor: palette.background,
    pieOuterStrokeColor: palette.border,
    quadrantPointFill: palette.accent,
    quadrantPointTextFill: palette.text,
    ...getMermaidScaleVariables(palette),
    xyChart: {
      backgroundColor: palette.background,
      titleColor: palette.text,
      xAxisTitleColor: palette.text,
      xAxisLabelColor: palette.mutedText,
      xAxisTickColor: palette.border,
      xAxisLineColor: palette.border,
      yAxisTitleColor: palette.text,
      yAxisLabelColor: palette.mutedText,
      yAxisTickColor: palette.border,
      yAxisLineColor: palette.border,
      plotColorPalette: [
        palette.accent,
        palette.accentSoft,
        palette.categorical[4],
        palette.categorical[6],
        palette.categorical[3],
      ].join(","),
    },
  }
}

/** Returns whether a code block should be shown as a Mermaid diagram. */
export function shouldRenderMermaidDiagram({
  isEditable,
  isFocused,
  language,
  renderWhenBlurred,
}: MermaidRenderState) {
  if (language.toLowerCase() !== "mermaid") {
    return false
  }

  return !isEditable || (renderWhenBlurred && !isFocused)
}

function getMermaidChartId(chart: string) {
  let hash = 0

  for (const char of chart) {
    hash = (hash << 5) - hash + char.charCodeAt(0)
    hash |= 0
  }

  return `case-mermaid-${Math.abs(hash)}-${Math.random().toString(36).slice(2, 9)}`
}

function getScopedMermaidCss(svgId: string, palette: MermaidPalette) {
  return `
#${svgId} {
  background: transparent !important;
  color: ${palette.text} !important;
  font-family: ${MERMAID_FONT_FAMILY} !important;
}

#${svgId} *,
#${svgId} foreignObject *,
#${svgId} span {
  box-shadow: none !important;
  filter: none !important;
  font-family: ${MERMAID_FONT_FAMILY} !important;
  text-shadow: none !important;
}

/*
 * Editor prose styles match p/span inside HTML labels directly and override
 * the font size Mermaid measured node boxes with, clipping label text.
 */
#${svgId} foreignObject div,
#${svgId} foreignObject span,
#${svgId} foreignObject p {
  font-size: inherit !important;
  margin: 0 !important;
}

#${svgId} .edgeLabel,
#${svgId} .label,
#${svgId} .label text,
#${svgId} .nodeLabel,
#${svgId} .legend text,
#${svgId} .titleText {
  color: ${palette.text} !important;
  fill: ${palette.text} !important;
}

#${svgId} .edgeLabel,
#${svgId} .edgeLabel p,
#${svgId} .edgeLabel span,
#${svgId} .edgeLabel rect,
#${svgId} .labelBkg {
  background-color: ${palette.background} !important;
  fill: ${palette.background} !important;
}

#${svgId} .flowchart-link,
#${svgId} .messageLine0,
#${svgId} .messageLine1,
#${svgId} .transition,
#${svgId} .relation,
#${svgId} .edge-thickness-normal,
#${svgId} .edge-thickness-thick {
  stroke: ${palette.line} !important;
}

/* Timeline node boxes draw a darker bottom edge that reads as a shadow. */
#${svgId} [class^="node-line"],
#${svgId} [class*=" node-line"] {
  stroke: transparent !important;
}

/* The timeline spine is drawn with a hardcoded thick black stroke. */
#${svgId} .lineWrapper line {
  stroke: ${palette.border} !important;
  stroke-width: 2px !important;
}

/* Timeline arrowheads fall back to near-black. */
#${svgId}[aria-roledescription="timeline"] marker path {
  fill: ${palette.arrowhead} !important;
}

/* Mindmap edges are drawn up to 11px thick. */
#${svgId}[aria-roledescription="mindmap"] .edge {
  stroke: ${palette.border} !important;
  stroke-width: 2px !important;
}

/* Round node-like rectangles; rx/ry are CSS geometry properties in SVG2. */
#${svgId} .node rect,
#${svgId} .cluster rect,
#${svgId} rect.actor,
#${svgId} rect.note,
#${svgId} .labelBox {
  rx: 6px;
  ry: 6px;
}

#${svgId} [class^="bar-plot"] rect,
#${svgId} [class*=" bar-plot"] rect {
  rx: 3px;
  ry: 3px;
}

/* Sankey nodes and links fall back to d3 rainbow colors. */
#${svgId}[aria-roledescription="sankey"] .node rect {
  fill: ${palette.sankeyNode} !important;
  stroke: none !important;
  rx: 2px;
  ry: 2px;
}

#${svgId}[aria-roledescription="sankey"] .links path {
  stroke: ${palette.sankeyLink} !important;
}

#${svgId} stop {
  stop-color: ${palette.accentSofter} !important;
}
`
}

function addScopedMermaidCss(
  svg: string,
  svgId: string,
  palette: MermaidPalette
) {
  return svg.replace(
    "</svg>",
    `<style>${getScopedMermaidCss(svgId, palette)}</style></svg>`
  )
}

const TIMELINE_NODE_BKG_PATH =
  /M0 ([\d.]+) v(-[\d.]+) q0,-5[,\s]5,-5 h([\d.]+) q5,0[,\s]5,5 v([\d.]+) H0 Z/g

/**
 * Timeline node backgrounds are paths with rounded top corners and square
 * bottom corners (the bottom edge was meant to be covered by the node line,
 * which is hidden here). Redraw them rounded on all four corners.
 */
function roundTimelineNodeCorners(svg: string) {
  return svg.replace(TIMELINE_NODE_BKG_PATH, (match, bottomY, _v, width) => {
    const y = Number.parseFloat(bottomY)
    const w = Number.parseFloat(width)

    if (!Number.isFinite(y) || !Number.isFinite(w) || y <= 5) {
      return match
    }

    return `M0 ${y} v${-(y - 5)} q0,-5,5,-5 h${w} q5,0,5,5 v${y - 5} q0,5,-5,5 h${-w} q-5,0,-5,-5 Z`
  })
}

function MermaidDiagram({ chart }: { chart: string }) {
  const { resolvedTheme } = useTheme()
  const theme: MermaidTheme = resolvedTheme === "dark" ? "dark" : "light"
  const [svg, setSvg] = React.useState("")
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => {
    let isMounted = true

    async function renderDiagram() {
      setSvg("")
      setError(null)

      try {
        const mermaid = (await import("mermaid")).default
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: "base",
          themeVariables: getMermaidThemeVariables(theme),
          fontFamily: MERMAID_FONT_FAMILY,
          suppressErrorRendering: true,
        })
        const chartId = getMermaidChartId(chart)
        const result = await mermaid.render(chartId, chart)

        if (isMounted) {
          setSvg(
            addScopedMermaidCss(
              roundTimelineNodeCorners(result.svg),
              chartId,
              getMermaidPalette(theme)
            )
          )
        }
      } catch (renderError) {
        if (isMounted) {
          setError(
            renderError instanceof Error
              ? renderError.message
              : "Failed to render Mermaid diagram"
          )
        }
      }
    }

    void renderDiagram()

    return () => {
      isMounted = false
    }
  }, [chart, theme])

  if (error) {
    return (
      <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm">
        <p className="font-medium text-destructive">Mermaid diagram error</p>
        <p className="mt-1 font-mono text-xs text-destructive/90">{error}</p>
        <details className="mt-3">
          <summary className="cursor-pointer text-xs text-muted-foreground">
            Show diagram source
          </summary>
          <pre className="mt-2 overflow-x-auto rounded-md bg-muted p-3 text-xs text-muted-foreground">
            {chart}
          </pre>
        </details>
      </div>
    )
  }

  if (!svg) {
    return (
      <div className="flex items-center justify-center p-4 text-sm text-muted-foreground">
        Rendering diagram...
      </div>
    )
  }

  return (
    <div
      aria-label="Mermaid diagram"
      className="max-w-full overflow-x-auto p-4 [&_svg]:mx-auto [&_svg]:h-auto [&_svg]:max-w-none"
      dangerouslySetInnerHTML={{ __html: svg }}
      role="img"
    />
  )
}

function getMermaidOptions(
  editor: Editor
): Pick<MermaidCodeBlockOptions, "renderWhenBlurred"> {
  const extension = editor.extensionManager.extensions.find(
    (item) => item.name === "codeBlock"
  )
  const options = extension?.options as Partial<MermaidCodeBlockOptions>

  return {
    renderWhenBlurred: options.renderWhenBlurred === true,
  }
}

function MermaidCodeBlockView({ editor, node }: NodeViewProps) {
  const [isFocused, setIsFocused] = React.useState(editor.isFocused)
  const language = String(node.attrs.language ?? "").toLowerCase()
  const chart = node.textContent
  const { renderWhenBlurred } = getMermaidOptions(editor)
  const shouldRenderDiagram = shouldRenderMermaidDiagram({
    isEditable: editor.isEditable,
    isFocused,
    language,
    renderWhenBlurred,
  })

  React.useEffect(() => {
    const updateFocus = () => setIsFocused(editor.isFocused)

    editor.on("focus", updateFocus)
    editor.on("blur", updateFocus)

    return () => {
      editor.off("focus", updateFocus)
      editor.off("blur", updateFocus)
    }
  }, [editor])

  if (shouldRenderDiagram) {
    return (
      <NodeViewWrapper
        as="div"
        className="my-4 overflow-hidden rounded-lg border border-border bg-background"
        data-mermaid-code-block="true"
      >
        <MermaidDiagram chart={chart} />
        <NodeViewContent as="div" className="hidden" />
      </NodeViewWrapper>
    )
  }

  return (
    <NodeViewWrapper
      as="pre"
      className={cn(language && `language-${language}`)}
    >
      <NodeViewContent<"code"> as="code" />
    </NodeViewWrapper>
  )
}

/** Tiptap code block extension that renders Mermaid fences in read-only views. */
export const MermaidCodeBlock = CodeBlock.extend<MermaidCodeBlockOptions>({
  addOptions() {
    return {
      languageClassPrefix: "language-",
      exitOnTripleEnter: true,
      exitOnArrowDown: true,
      defaultLanguage: null,
      enableTabIndentation: false,
      tabSize: 4,
      HTMLAttributes: {},
      ...this.parent?.(),
      exitOnArrowUp: false,
      renderWhenBlurred: false,
    }
  },

  addNodeView() {
    return ReactNodeViewRenderer(MermaidCodeBlockView)
  },
})
