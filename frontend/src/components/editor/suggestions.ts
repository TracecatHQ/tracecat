import {
  EditorFunctionRead,
  editorListActions,
  editorListFunctions,
  EditorParamRead,
} from "@/client"
import { Monaco } from "@monaco-editor/react"
import { IMarkdownString, IRange, languages } from "monaco-editor"

import { siteConfig } from "@/config/site"

export const INSIDE_EXPR_PATTERN = /\$\{\{(?![^{]*\}\})/
export const ACTION_CONTEXT_PATTERN = /ACTIONS\.[\.\w+]*$/
export const ACTION_REF_PATTERN = /ACTIONS\.(\w+)\.$/
export const ACTIONS_RES_ERR_PATTERN = /ACTIONS\.(\w+)\.(result|error)\.$/
export const ACTION_JSONPATH_PATTERN = /ACTIONS\.(\w{2,}\.)+$/

export const EXPR_CONTEXTS = [
  "var",
  "FN",
  "ACTIONS",
  "ENV",
  "TRIGGER",
  "INPUTS",
  "SECRETS",
] as const

interface ExprContextInfo {
  key: (typeof EXPR_CONTEXTS)[number]
  documentation: string
}

const exprContextInfo: ExprContextInfo[] = [
  {
    key: "ACTIONS",
    documentation: "Insert action context reference",
  },
  {
    key: "FN",
    documentation: "Insert function reference",
  },
  {
    key: "ENV",
    documentation: "Insert environment variable reference",
  },
  {
    key: "TRIGGER",
    documentation: "Insert trigger context reference",
  },
  {
    key: "INPUTS",
    documentation: "Insert input context reference",
  },
  {
    key: "SECRETS",
    documentation: "Insert secret context reference",
  },
  {
    key: "var",
    documentation: "Insert action-local variable reference",
  },
]

// If the last character is a {, insert padding
export function formatContextPadding(text: string, pad: boolean = false) {
  return pad ? ` ${text}$0 ` : text
}

export async function getActionCompletions(
  monaco: Monaco,
  range: IRange,
  workspaceId: string,
  workflowId: string
): Promise<languages.CompletionItem[]> {
  try {
    const editorActions = await editorListActions({
      workspaceId,
      workflowId,
    })
    return editorActions.map((a) => ({
      label: a.ref,
      kind: languages.CompletionItemKind.Field,
      insertText: a.ref,
      range,
    }))
  } catch {
    console.log("Couldn't fetch action completions")
    return []
  }
}

function getFunctionSuggestionDocs(fn: EditorFunctionRead): IMarkdownString {
  // Create parameter list for function signature
  const parameterList = fn.parameters
    .map((p) => `${p.name}: ${p.type}`)
    .join(", ")

  return {
    value: [
      "```python",
      `def ${fn.name}(${parameterList}) -> ${fn.return_type}`,
      "```",
      "",
      fn.description,
      "",
      "**Parameters:**",
      ...fn.parameters.map(
        (p: EditorParamRead) =>
          `- \`${p.name}\`: ${p.type}${p.optional ? " (optional)" : ""}`
      ),
      "",
      `**Returns:** \`${fn.return_type}\``,
    ].join("\n"),
    isTrusted: true,
  }
}

export async function getFunctionSuggestions(
  monaco: Monaco,
  range: IRange,
  workspaceId: string
): Promise<languages.CompletionItem[]> {
  const functions = await editorListFunctions({ workspaceId })
  return (
    functions?.map((fn) => {
      // Create parameter snippet with placeholders
      const params = fn.parameters
        .map((p: EditorParamRead, i: number) => `\${${i + 1}:${p.name}}`)
        .join(", ")

      return {
        label: fn.name,
        kind: languages.CompletionItemKind.Function,
        insertText: `${fn.name}(${params})`,
        detail: fn.return_type,
        documentation: getFunctionSuggestionDocs(fn),
        insertTextRules: languages.CompletionItemInsertTextRule.InsertAsSnippet,
        range,
      }
    }) ?? []
  )
}

export function getContextSuggestions(
  monaco: Monaco,
  range: IRange
): languages.CompletionItem[] {
  return exprContextInfo.map((completion) => ({
    label: completion.key,
    kind: monaco.languages.CompletionItemKind.Keyword,
    insertText: completion.key,
    documentation: completion.documentation,
    range,
  }))
}

export function getEnvCompletions(range: IRange): languages.CompletionItem[] {
  return []
}

export function getInputCompletions(range: IRange): languages.CompletionItem[] {
  return []
}

export function getSecretCompletions(
  range: IRange
): languages.CompletionItem[] {
  return []
}

export function getTriggerCompletions(
  range: IRange
): languages.CompletionItem[] {
  return []
}

export function getExpressionCompletions(
  range: IRange
): languages.CompletionItem[] {
  return [
    {
      label: "expression (${{ ... }})",
      kind: languages.CompletionItemKind.Keyword,
      insertText: "{{ $0 }}",
      insertTextRules: languages.CompletionItemInsertTextRule.InsertAsSnippet,
      documentation: {
        value: `Insert expression. Docs: ${siteConfig.links.docs}/platform/expressions`,
        isTrusted: true,
        supportHtml: true,
      },
      range,
    },
  ]
}
