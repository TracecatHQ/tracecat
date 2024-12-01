import { Monaco } from "@monaco-editor/react"
import { IRange, languages } from "monaco-editor"

// If the last character is a {, insert padding
export const formatContextPadding = (text: string, pad: boolean = false) =>
  pad ? ` ${text}$0 ` : text

export const getContextSuggestions = (
  monaco: Monaco,
  range: IRange
): languages.CompletionItem[] => {
  return [
    {
      label: "var",
      kind: monaco.languages.CompletionItemKind.Keyword,
      insertText: "var",
      documentation: "Insert action-local variable reference",
      range,
    },
    {
      label: "FN",
      kind: monaco.languages.CompletionItemKind.Keyword,
      insertText: "FN",
      documentation: "Insert function reference",
      range,
    },
    {
      label: "ACTIONS",
      kind: monaco.languages.CompletionItemKind.Keyword,
      insertText: "ACTIONS",
      documentation: "Insert action context reference",
      range,
    },
  ]
}
// Define completion items for different contexts
export const getActionCompletions = (
  range: IRange
): languages.CompletionItem[] => {
  return [
    {
      label: "previous",
      kind: languages.CompletionItemKind.Field,
      insertText: "previous",
      documentation: "Reference to previous action result",
      range,
    },
    {
      label: "current",
      kind: languages.CompletionItemKind.Field,
      insertText: "current",
      documentation: "Reference to current action context",
      range,
    },
  ]
}
