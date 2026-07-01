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

const MERMAID_THEME_VARIABLES = {
  fontFamily: MERMAID_FONT_FAMILY,
  background: "#ffffff",
  mainBkg: "#f4f4f5",
  secondBkg: "#f8fafc",
  tertiaryColor: "#ffffff",
  primaryColor: "#f4f4f5",
  secondaryColor: "#f8fafc",
  primaryBorderColor: "#a1a1aa",
  secondaryBorderColor: "#cbd5e1",
  tertiaryBorderColor: "#d4d4d8",
  primaryTextColor: "#18181b",
  secondaryTextColor: "#18181b",
  tertiaryTextColor: "#18181b",
  textColor: "#18181b",
  titleColor: "#18181b",
  darkTextColor: "#18181b",
  lineColor: "#71717a",
  nodeBorder: "#a1a1aa",
  clusterBkg: "#fafafa",
  clusterBorder: "#d4d4d8",
  defaultLinkColor: "#71717a",
  edgeLabelBackground: "#ffffff",
  actorBkg: "#f4f4f5",
  actorBorder: "#a1a1aa",
  actorTextColor: "#18181b",
  actorLineColor: "#71717a",
  signalColor: "#52525b",
  signalTextColor: "#18181b",
  labelBoxBkgColor: "#f4f4f5",
  labelBoxBorderColor: "#a1a1aa",
  labelTextColor: "#18181b",
  loopTextColor: "#18181b",
  noteBkgColor: "#fafafa",
  noteBorderColor: "#d4d4d8",
  noteTextColor: "#18181b",
  activationBkgColor: "#e4e4e7",
  activationBorderColor: "#a1a1aa",
  sectionBkgColor: "#f4f4f5",
  altSectionBkgColor: "#ffffff",
  taskBkgColor: "#f4f4f5",
  taskTextColor: "#18181b",
  taskTextOutsideColor: "#18181b",
  taskBorderColor: "#a1a1aa",
  activeTaskBkgColor: "#dbeafe",
  activeTaskBorderColor: "#93c5fd",
  doneTaskBkgColor: "#e4e4e7",
  doneTaskBorderColor: "#a1a1aa",
  critBkgColor: "#fee2e2",
  critBorderColor: "#fca5a5",
  gridColor: "#e4e4e7",
  cScale0: "#e4e4e7",
  cScale1: "#dbeafe",
  cScale2: "#bfdbfe",
  cScale3: "#93c5fd",
  pie1: "#e4e4e7",
  pie2: "#dbeafe",
  pie3: "#bfdbfe",
  pie4: "#93c5fd",
  pie5: "#cbd5e1",
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
  color: #18181b !important;
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

#${svgId} .edgeLabel,
#${svgId} .label,
#${svgId} .label text,
#${svgId} .nodeLabel,
#${svgId} .legend text,
#${svgId} .titleText {
  color: #18181b !important;
  fill: #18181b !important;
}

#${svgId} .flowchart-link,
#${svgId} .messageLine0,
#${svgId} .messageLine1,
#${svgId} .transition,
#${svgId} .relation,
#${svgId} .edge-thickness-normal,
#${svgId} .edge-thickness-thick {
  stroke: #71717a !important;
}

#${svgId} [class^="node-line"],
#${svgId} [class*=" node-line"] {
  stroke: #a1a1aa !important;
}

#${svgId} .bar-plot-0 rect,
#${svgId} .bar-plot-1 rect,
#${svgId} .bar-plot-2 rect {
  fill: #dbeafe !important;
  stroke: #93c5fd !important;
}

#${svgId} .nodes .node rect {
  fill: #64748b !important;
  stroke: #475569 !important;
}

#${svgId} .line-plot-0 path,
#${svgId} .line-plot-1 path,
#${svgId} .line-plot-2 path {
  stroke: #2563eb !important;
}

#${svgId} .links path,
#${svgId} stop {
  stop-color: #bfdbfe !important;
  stroke: #93c5fd !important;
}
`
}

function addScopedMermaidCss(svg: string, svgId: string) {
  return svg.replace(
    "</svg>",
    `<style>${getScopedMermaidCss(svgId)}</style></svg>`
  )
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
          setSvg(addScopedMermaidCss(result.svg, chartId))
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
