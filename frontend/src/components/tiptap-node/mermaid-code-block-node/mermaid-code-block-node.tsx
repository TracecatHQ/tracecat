"use client"

import { CodeBlock } from "@tiptap/extension-code-block"
import {
  mergeAttributes,
  NodeViewContent,
  type NodeViewProps,
  NodeViewWrapper,
  ReactNodeViewRenderer,
} from "@tiptap/react"
import * as React from "react"
import { cn } from "@/lib/utils"

function getMermaidChartId(chart: string) {
  let hash = 0

  for (const char of chart) {
    hash = (hash << 5) - hash + char.charCodeAt(0)
    hash |= 0
  }

  return `case-mermaid-${Math.abs(hash)}-${Math.random().toString(36).slice(2, 9)}`
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
          theme: "default",
          fontFamily: "monospace",
          suppressErrorRendering: true,
        })
        const result = await mermaid.render(getMermaidChartId(chart), chart)

        if (isMounted) {
          setSvg(result.svg)
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
      className="overflow-x-auto p-4 [&_svg]:mx-auto [&_svg]:max-w-full"
      dangerouslySetInnerHTML={{ __html: svg }}
      role="img"
    />
  )
}

function MermaidCodeBlockView({ editor, node }: NodeViewProps) {
  const language = String(node.attrs.language ?? "").toLowerCase()
  const chart = node.textContent
  const shouldRenderDiagram = !editor.isEditable && language === "mermaid"

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
export const MermaidCodeBlock = CodeBlock.extend({
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
