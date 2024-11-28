import { useCallback, useEffect, useRef } from "react"
import { ParameterMeta } from "@/client"
import {
  EditorProps,
  Editor as ReactMonacoEditor,
  type Monaco,
} from "@monaco-editor/react"
import { editor, IDisposable, IRange, languages } from "monaco-editor"

import { useEditorFunctions } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { CenteredSpinner } from "@/components/loading/spinner"

const getContextSuggestions = (monaco: Monaco, range: IRange) => {
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

export function CustomEditor({
  className,
  onKeyDown,
  ...props
}: EditorProps & {
  className?: string
  onKeyDown?: () => void
}) {
  const { functions } = useEditorFunctions()
  const completionDisposableRef = useRef<IDisposable | null>(null)
  const hoverDisposableRef = useRef<IDisposable | null>(null)

  const getFunctionCompletions = useCallback(
    (range: IRange): languages.CompletionItem[] => {
      if (!functions) {
        return []
      }
      // Fetch available functions from the backend
      return functions.map((fn) => {
        // Create parameter snippet with placeholders
        const params = fn.parameters
          .map((p: ParameterMeta, i: number) => `\${${i + 1}:${p.name}}`)
          .join(", ")

        return {
          label: fn.name,
          kind: languages.CompletionItemKind.Function,
          insertText: `${fn.name}(${params})`,
          documentation: {
            value: [
              fn.description,
              "",
              "Parameters:",
              ...fn.parameters.map(
                (p: ParameterMeta) =>
                  `- ${p.name}: ${p.type}${p.optional ? " (optional)" : ""}`
              ),
              "",
              `Returns: ${fn.return_type}`,
            ].join("\n"),
          },
          insertTextRules:
            languages.CompletionItemInsertTextRule.InsertAsSnippet,
          range,
        }
      })
    },
    [functions]
  )

  const handleEditorDidMount = useCallback(
    (editor: editor.IStandaloneCodeEditor, monaco: Monaco) => {
      // Cleanup previous providers if they exist
      completionDisposableRef.current?.dispose()
      hoverDisposableRef.current?.dispose()

      // Customize the editor using the monaco instance
      monaco.editor.defineTheme("myCustomTheme", {
        base: "vs", // can also be 'vs', 'hc-black'
        inherit: true, // can also be false to completely replace the base theme
        rules: [
          // {
          //   token: "comment",
          //   foreground: "ffa500",
          //   fontStyle: "italic underline",
          // },
          // { token: "keyword", foreground: "00ff00" },
          // { token: "identifier", foreground: "ff0000" },
          // Add more token styles here
          // Add custom rules for the suggestion widget
        ],
        colors: {
          // "editor.foreground": "#F8F8F8",
          // "editor.background": "#efefef",
          // "editorCursor.foreground": "#A7A7A7",
          "editor.lineHighlightBackground": "#e8f7ff",
          "editorLineNumber.foreground": "#5A5A5A",
          "editor.selectionBackground": "#88000030",
          "editor.inactiveSelectionBackground": "#88000015",
          // Suggestion widget colors
          "editorSuggestWidget.background": "#ffffff", // Widget background
          "editorSuggestWidget.border": "#e0e0e0", // Widget border
          "editorSuggestWidget.foreground": "#000000", // Text color
          "editorSuggestWidget.selectedBackground": "#0078d4", // Selected item background
          "editorSuggestWidget.selectedForeground": "#ffffff", // Selected item text
          "editorSuggestWidget.highlightForeground": "#0078d4", // Matching text highlight color
          "editorSuggestWidget.focusHighlightForeground": "#0078d4", // Focused item highlight color
          // Add more color customizations here
        },
      })
      monaco.editor.setTheme("myCustomTheme")

      // Define completion items for different contexts
      const getActionCompletions = (
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

      // Register and store the completion provider
      completionDisposableRef.current =
        monaco.languages.registerCompletionItemProvider("yaml", {
          triggerCharacters: ["$", "{", ".", " "],
          provideCompletionItems: async (model, position, context, token) => {
            const wordUntilPosition = model.getWordUntilPosition(position)
            const lineContent = model.getLineContent(position.lineNumber)
            const textUntilPosition = lineContent.substring(
              0,
              position.column - 1
            )

            const range: IRange = {
              startLineNumber: position.lineNumber,
              endLineNumber: position.lineNumber,
              startColumn: wordUntilPosition.startColumn,
              endColumn: position.column,
            }

            // Add early returns to prevent multiple triggers
            if (textUntilPosition.match(/.*\$\{\{\s*\}\}/)) {
              return { suggestions: getContextSuggestions(monaco, range) }
            }

            if (textUntilPosition.match(/\$\{\{\s*ACTIONS\s*\.\s*$/)) {
              return { suggestions: getActionCompletions(range) }
            }

            if (textUntilPosition.match(/\$\{\{\s*FN\s*\.\s*$/)) {
              return { suggestions: getFunctionCompletions(range) }
            }

            // Consolidate the ${{ triggers into one condition
            const isStartingExpression =
              textUntilPosition.endsWith("$") ||
              textUntilPosition.trimEnd().endsWith("${{")

            if (isStartingExpression) {
              // Return either the expression starter or context suggestions, not both
              if (textUntilPosition.endsWith("$")) {
                return {
                  suggestions: [
                    {
                      label: "expression (${{ ... }})",
                      kind: monaco.languages.CompletionItemKind.Keyword,
                      insertText: "{{$0}}",
                      insertTextRules:
                        monaco.languages.CompletionItemInsertTextRule
                          .InsertAsSnippet,
                      documentation: "Insert action-local variable reference",
                      range,
                    },
                  ],
                }
              } else {
                return {
                  suggestions: getContextSuggestions(monaco, range).map(
                    (s) => ({
                      ...s,
                      insertText: `${s.insertText}`,
                      range,
                    })
                  ),
                }
              }
            }

            return { suggestions: [] }
          },
        })

      // Register and store the hover provider
      hoverDisposableRef.current = monaco.languages.registerHoverProvider(
        "yaml",
        {
          provideHover: (model, position) => {
            const wordAtPosition = model.getWordAtPosition(position)
            if (!wordAtPosition) return null

            const word = wordAtPosition.word
            const lineContent = model.getLineContent(position.lineNumber)

            // Check if we're in a FN context
            if (lineContent.includes("FN.") && functions) {
              const fn = functions.find((f) => f.name === word)
              if (fn) {
                return {
                  contents: [
                    {
                      value: [
                        "```python",
                        `def ${fn.name}(${fn.parameters
                          .map(
                            (p) =>
                              `${p.name}: ${p.type}${p.optional ? " (optional)" : ""}`
                          )
                          .join(", ")}) -> ${fn.return_type}`,
                        "```",
                        "",
                        fn.description,
                        "",
                        "**Parameters:**",
                        ...fn.parameters.map(
                          (p: ParameterMeta) =>
                            `- \`${p.name}\`: ${p.type}${p.optional ? " (optional)" : ""}`
                        ),
                        "",
                        `**Returns:** ${fn.return_type}`,
                      ].join("\n"),
                      isTrusted: true,
                      supportHtml: true,
                    },
                  ],
                }
              }
            }

            return null
          },
        }
      )
    },
    [functions]
  )

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      completionDisposableRef.current?.dispose()
      hoverDisposableRef.current?.dispose()
    }
  }, [])

  return (
    <>
      {/* Add an overflow container at the root level */}
      <div className={cn("h-36", className)}>
        <ReactMonacoEditor
          height="100%"
          loading={<CenteredSpinner />}
          onMount={handleEditorDidMount}
          theme="myCustomTheme"
          language="yaml"
          options={{
            tabSize: 2,
            minimap: { enabled: false },
            scrollbar: {
              verticalScrollbarSize: 5,
              horizontalScrollbarSize: 5,
            },
            renderLineHighlight: "all",
            automaticLayout: true,
            fixedOverflowWidgets: true,
            ...props.options,
          }}
          wrapperProps={{ className: "editor-wrapper" }}
          {...props}
        />
      </div>
    </>
  )
}
