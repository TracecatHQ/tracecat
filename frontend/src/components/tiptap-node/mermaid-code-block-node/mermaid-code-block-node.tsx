"use client"

import { CodeBlock, type CodeBlockOptions } from "@tiptap/extension-code-block"
import {
  type Editor,
  mergeAttributes,
  NodeViewContent,
  type NodeViewProps,
  NodeViewWrapper,
  ReactNodeViewRenderer,
} from "@tiptap/react"
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

const MERMAID_TEXT_COLOR = "#18181b"
const MERMAID_MUTED_TEXT_COLOR = "#71717a"
const MERMAID_LINE_COLOR = "#71717a"
const MERMAID_BORDER_COLOR = "#d4d4d8"
const MERMAID_SURFACE_COLOR = "#f4f4f5"
const MERMAID_SURFACE_SUBTLE_COLOR = "#fafafa"
const MERMAID_ACCENT_COLOR = "#3b82f6"

/**
 * Cool pastel ramp shared by section scales (`cScale*`), pie slices, and git
 * branches. Mermaid falls back to rainbow defaults for any index left unset,
 * so all twelve slots are defined.
 */
const MERMAID_CATEGORICAL_COLORS = [
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
] as const

function getMermaidScaleVariables() {
  const variables: Record<string, string> = {}

  MERMAID_CATEGORICAL_COLORS.forEach((color, index) => {
    variables[`cScale${index}`] = color
    variables[`cScaleLabel${index}`] = MERMAID_TEXT_COLOR
    variables[`pie${index + 1}`] = color

    if (index < 8) {
      variables[`git${index}`] = color
      variables[`gitBranchLabel${index}`] = MERMAID_TEXT_COLOR
    }
  })

  return variables
}

const MERMAID_THEME_VARIABLES = {
  fontFamily: MERMAID_FONT_FAMILY,
  fontSize: "12px",
  background: "#ffffff",
  mainBkg: MERMAID_SURFACE_COLOR,
  secondBkg: MERMAID_SURFACE_SUBTLE_COLOR,
  tertiaryColor: "#ffffff",
  primaryColor: MERMAID_SURFACE_COLOR,
  secondaryColor: MERMAID_SURFACE_SUBTLE_COLOR,
  primaryBorderColor: MERMAID_BORDER_COLOR,
  secondaryBorderColor: MERMAID_BORDER_COLOR,
  tertiaryBorderColor: MERMAID_BORDER_COLOR,
  primaryTextColor: MERMAID_TEXT_COLOR,
  secondaryTextColor: MERMAID_TEXT_COLOR,
  tertiaryTextColor: MERMAID_TEXT_COLOR,
  textColor: MERMAID_TEXT_COLOR,
  titleColor: MERMAID_TEXT_COLOR,
  darkTextColor: MERMAID_TEXT_COLOR,
  lineColor: MERMAID_LINE_COLOR,
  arrowheadColor: MERMAID_LINE_COLOR,
  nodeBorder: MERMAID_BORDER_COLOR,
  clusterBkg: MERMAID_SURFACE_SUBTLE_COLOR,
  clusterBorder: "#e4e4e7",
  defaultLinkColor: MERMAID_LINE_COLOR,
  edgeLabelBackground: "#ffffff",
  actorBkg: MERMAID_SURFACE_COLOR,
  actorBorder: MERMAID_BORDER_COLOR,
  actorTextColor: MERMAID_TEXT_COLOR,
  actorLineColor: MERMAID_LINE_COLOR,
  signalColor: MERMAID_LINE_COLOR,
  signalTextColor: MERMAID_TEXT_COLOR,
  labelBoxBkgColor: MERMAID_SURFACE_COLOR,
  labelBoxBorderColor: MERMAID_BORDER_COLOR,
  labelTextColor: MERMAID_TEXT_COLOR,
  loopTextColor: MERMAID_TEXT_COLOR,
  noteBkgColor: MERMAID_SURFACE_SUBTLE_COLOR,
  noteBorderColor: MERMAID_BORDER_COLOR,
  noteTextColor: MERMAID_TEXT_COLOR,
  activationBkgColor: "#e4e4e7",
  activationBorderColor: MERMAID_BORDER_COLOR,
  sectionBkgColor: MERMAID_SURFACE_COLOR,
  altSectionBkgColor: "#ffffff",
  taskBkgColor: "#dbeafe",
  taskTextColor: MERMAID_TEXT_COLOR,
  taskTextOutsideColor: MERMAID_TEXT_COLOR,
  taskBorderColor: "#93c5fd",
  activeTaskBkgColor: "#93c5fd",
  activeTaskBorderColor: "#60a5fa",
  doneTaskBkgColor: "#e4e4e7",
  doneTaskBorderColor: MERMAID_BORDER_COLOR,
  critBkgColor: "#fee2e2",
  critBorderColor: "#fca5a5",
  todayLineColor: "#f87171",
  gridColor: "#e4e4e7",
  pieOpacity: "1",
  pieStrokeColor: "#ffffff",
  pieOuterStrokeColor: "#e4e4e7",
  quadrantPointFill: MERMAID_ACCENT_COLOR,
  quadrantPointTextFill: MERMAID_TEXT_COLOR,
  ...getMermaidScaleVariables(),
  xyChart: {
    backgroundColor: "#ffffff",
    titleColor: MERMAID_TEXT_COLOR,
    xAxisTitleColor: MERMAID_TEXT_COLOR,
    xAxisLabelColor: MERMAID_MUTED_TEXT_COLOR,
    xAxisTickColor: MERMAID_BORDER_COLOR,
    xAxisLineColor: MERMAID_BORDER_COLOR,
    yAxisTitleColor: MERMAID_TEXT_COLOR,
    yAxisLabelColor: MERMAID_MUTED_TEXT_COLOR,
    yAxisTickColor: MERMAID_BORDER_COLOR,
    yAxisLineColor: MERMAID_BORDER_COLOR,
    plotColorPalette: [
      MERMAID_ACCENT_COLOR,
      "#93c5fd",
      "#a5b4fc",
      "#7dd3fc",
      "#c7d2fe",
    ].join(","),
  },
} as const

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

function getScopedMermaidCss(svgId: string) {
  return `
#${svgId} {
  background: transparent !important;
  color: ${MERMAID_TEXT_COLOR} !important;
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
  color: ${MERMAID_TEXT_COLOR} !important;
  fill: ${MERMAID_TEXT_COLOR} !important;
}

#${svgId} .flowchart-link,
#${svgId} .messageLine0,
#${svgId} .messageLine1,
#${svgId} .transition,
#${svgId} .relation,
#${svgId} .edge-thickness-normal,
#${svgId} .edge-thickness-thick {
  stroke: ${MERMAID_LINE_COLOR} !important;
}

/* Timeline node boxes draw a darker bottom edge that reads as a shadow. */
#${svgId} [class^="node-line"],
#${svgId} [class*=" node-line"] {
  stroke: transparent !important;
}

/* The timeline spine is drawn with a hardcoded thick black stroke. */
#${svgId} .lineWrapper line {
  stroke: ${MERMAID_BORDER_COLOR} !important;
  stroke-width: 2px !important;
}

/* Timeline arrowheads fall back to near-black. */
#${svgId}[aria-roledescription="timeline"] marker path {
  fill: #a1a1aa !important;
}

/* Mindmap edges are drawn up to 11px thick. */
#${svgId}[aria-roledescription="mindmap"] .edge {
  stroke: ${MERMAID_BORDER_COLOR} !important;
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
  fill: #60a5fa !important;
  stroke: none !important;
  rx: 2px;
  ry: 2px;
}

#${svgId}[aria-roledescription="sankey"] .links path {
  stroke: #bfdbfe !important;
}

#${svgId} stop {
  stop-color: #bfdbfe !important;
}
`
}

function addScopedMermaidCss(svg: string, svgId: string) {
  return svg.replace(
    "</svg>",
    `<style>${getScopedMermaidCss(svgId)}</style></svg>`
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
          themeVariables: MERMAID_THEME_VARIABLES,
          fontFamily: MERMAID_FONT_FAMILY,
          suppressErrorRendering: true,
        })
        const chartId = getMermaidChartId(chart)
        const result = await mermaid.render(chartId, chart)

        if (isMounted) {
          setSvg(
            addScopedMermaidCss(roundTimelineNodeCorners(result.svg), chartId)
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
  }, [chart])

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
      </NodeViewWrapper>
    )
  }

  return (
    <NodeViewWrapper
      as="pre"
      className={cn(language && `language-${language}`)}
    >
      <NodeViewContent />
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
      renderWhenBlurred: false,
    }
  },

  addNodeView() {
    return ReactNodeViewRenderer(MermaidCodeBlockView)
  },

  renderHTML({ HTMLAttributes }) {
    return [
      "pre",
      mergeAttributes(this.options.HTMLAttributes, HTMLAttributes),
      ["code", 0],
    ]
  },
})
