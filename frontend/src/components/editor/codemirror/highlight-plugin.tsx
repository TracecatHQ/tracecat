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
          padding: 0.125rem 0.25rem;
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
export function createTemplateHoverTooltip() {
  return hoverTooltip(async (view, pos) => {
    const doc = view.state.doc.toString()
    const templateRegex = createTemplateRegex()

    let match
    while ((match = templateRegex.exec(doc)) !== null) {
      const from = match.index
      const to = match.index + match[0].length

      if (pos >= from && pos <= to) {
        const expression = match[1]?.trim()
        if (!expression) return null

        // Simple tooltip without validation for now
        return {
          pos: from,
          end: to,
          above: true,
          create: () => {
            const dom = document.createElement("div")
            dom.className = "template-tooltip"
            dom.style.cssText = `
              background: white;
              border: 1px solid #ccc;
              border-radius: 4px;
              padding: 8px;
              box-shadow: 0 2px 10px rgba(0,0,0,0.1);
              max-width: 300px;
            `
            dom.textContent = `Expression: ${expression}`
            return { dom }
          },
        }
      }
    }

    return null
  })
}

/**
 * Combined plugin that includes highlighting and hover tooltips
 */
export function createSimpleTemplatePlugin() {
  return [createTemplateHighlightPlugin(), createTemplateHoverTooltip()]
}
