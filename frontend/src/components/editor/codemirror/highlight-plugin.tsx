/**
 * Simple Template Expression Highlighting Plugin
 *
 * This plugin provides basic syntax highlighting for template expressions
 * without the complex pill interaction system. It highlights ${{ }} blocks
 * with a background color and provides basic validation and hover tooltips.
 */

import type { EditorState, Range } from "@codemirror/state"
import {
  Decoration,
  type DecorationSet,
  type EditorView,
  hoverTooltip,
  ViewPlugin,
  type ViewUpdate,
} from "@codemirror/view"
import { createNodeTooltipForPosition } from "@/components/editor/codemirror/common"
import { createTemplateRegex } from "@/lib/expressions"

/**
 * Create decorations for template expressions with simple highlighting
 */
function createTemplateHighlightDecorations(state: EditorState): DecorationSet {
  const decorations: Range<Decoration>[] = []
  const templateRegex = createTemplateRegex()
  const doc = state.doc.toString()

  let match
  while ((match = templateRegex.exec(doc)) !== null) {
    const from = match.index
    const to = match.index + match[0].length

    // Create a simple background highlight decoration
    const decoration = Decoration.mark({
      class: "template-expression-highlight",
      attributes: {
        style: `
          background-color: rgb(59 130 246 / 0.1);
          color: rgb(55 65 81 / 0.9);
          border-radius: 0.25rem;
          padding: 0.05rem 0.125rem;
          border: 1px solid rgb(59 130 246 / 0.2);
          font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, "SF Mono", Consolas, "Liberation Mono", Menlo, monospace;
        `,
      },
    })

    decorations.push(decoration.range(from, to))
  }

  return Decoration.set(decorations)
}

/**
 * Plugin view for template expression highlighting
 */
class TemplateHighlightPluginView {
  decorations: DecorationSet

  constructor(view: EditorView) {
    this.decorations = createTemplateHighlightDecorations(view.state)
  }

  update(update: ViewUpdate) {
    if (update.docChanged || update.viewportChanged) {
      this.decorations = createTemplateHighlightDecorations(update.state)
    }
  }
}

/**
 * Create the template expression highlighting plugin
 */
export function createTemplateHighlightPlugin() {
  return ViewPlugin.fromClass(TemplateHighlightPluginView, {
    decorations: (v) => v.decorations,
  })
}

/**
 * Create hover tooltip for template expressions
 */
export function createTemplateHoverTooltip(workspaceId: string) {
  return hoverTooltip(async (view, pos) => {
    // Find if the position is within a template expression
    const doc = view.state.doc
    const line = doc.lineAt(pos)
    const lineText = line.text

    // Find all template expressions in the line
    const templateRegex = createTemplateRegex()
    let match
    templateRegex.lastIndex = 0

    while ((match = templateRegex.exec(lineText)) !== null) {
      const templateStart = line.from + match.index
      const templateEnd = templateStart + match[0].length

      // Check if position is within this template expression
      if (pos >= templateStart && pos <= templateEnd) {
        const innerContent = match[1].trim()
        const innerStart = templateStart + match[0].indexOf(innerContent)
        const innerEnd = innerStart + innerContent.length

        // Check if position is within the inner content
        if (pos >= innerStart && pos <= innerEnd) {
          const relativePos = pos - innerStart
          return await createNodeTooltipForPosition(
            view,
            innerContent,
            relativePos,
            templateStart,
            workspaceId
          )
        }
      }
    }

    return null
  })
}

/**
 * Combined plugin that includes highlighting and hover tooltips
 */
export function createSimpleTemplatePlugin(workspaceId: string) {
  return [
    createTemplateHighlightPlugin(),
    createTemplateHoverTooltip(workspaceId),
  ]
}
