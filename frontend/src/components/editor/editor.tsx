"use client"

import { useEffect, useRef } from "react"
import { EditorFunctionRead, editorListFunctions } from "@/client"
import {
  EditorProps,
  Editor as ReactMonacoEditor,
  type Monaco,
} from "@monaco-editor/react"
import { editor, IDisposable, IRange, languages } from "monaco-editor"

import { cn } from "@/lib/utils"
import { EverforestLightMed } from "@/components/editor/styles"
import {
  ACTION_CONTEXT_PATTERN,
  ACTION_REF_PATTERN,
  ACTIONS_RES_ERR_PATTERN,
  EXPR_CONTEXTS,
  formatContextPadding,
  getActionCompletions,
  getContextSuggestions,
  getEnvCompletions,
  getExpressionCompletions,
  getFunctionSuggestions,
  getInputCompletions,
  getSecretCompletions,
  getTriggerCompletions,
  INSIDE_EXPR_PATTERN,
} from "@/components/editor/suggestions"
import {
  conf as yamlConf,
  language as yamlLanguage,
} from "@/components/editor/yaml-lang"
import { CenteredSpinner } from "@/components/loading/spinner"

const constructDslLang = (): {
  conf: languages.LanguageConfiguration
  lang: languages.IMonarchLanguage
} => {
  const tracecatDslLang: languages.IMonarchLanguage = {
    tokenizer: {
      root: [
        // Keywords first
        [
          /\s*\b(ACTIONS|SECRETS|ENV|INPUTS|TRIGGER|FN|var)\b/,
          "expr.context.global",
        ],
        [
          /\b(FN)\b(\.)(\w+)/,
          ["expr.context.global", "expr.operator", "expr.function.name"],
        ],
        // Parameters
        [/[a-zA-Z_]\w*(?=\s*[,)])/, "expr.parameter"],
        // Operators
        [/[.,\[\]()]/, "expr.operator"], // Added [] to operators
        // Base expressions
        [/[^}]+?(?=\}\})/, "expr.base"], // Non-greedy match until }}
      ],
    },
  }

  return { conf: yamlConf, lang: tracecatDslLang }
}

interface FunctionDocumentation {
  contents: Array<{
    value: string
    isTrusted: boolean
    supportHtml: boolean
  }>
}

/**
 * Generates markdown documentation for a function
 * @param fn Function metadata including name, parameters, return type, and description
 * @returns Formatted documentation object for Monaco editor
 */
function generateFunctionDocs(
  fn: EditorFunctionRead | null
): FunctionDocumentation | undefined {
  if (!fn) {
    return undefined
  }

  const parameterList = fn.parameters
    .map((p) => `${p.name}: ${p.type}${p.optional ? " (optional)" : ""}`)
    .join(", ")

  const parameterDocs = fn.parameters.map(
    (p) => `- \`${p.name}\`: ${p.type}${p.optional ? " (optional)" : ""}`
  )

  const docContent = [
    "```python",
    `def ${fn.name}(${parameterList}) -> ${fn.return_type}`,
    "```",
    "",
    fn.description,
    "",
    "**Parameters:**",
    ...parameterDocs,
    "",
    `**Returns:** ${fn.return_type}`,
  ].join("\n")

  return {
    contents: [
      {
        value: docContent,
        isTrusted: true,
        supportHtml: true,
      },
    ],
  }
}

interface ISuggestController {
  widget: {
    value?: {
      _setDetailsVisible: (visible: boolean) => void
      _persistedSize?: {
        store: (size: { width: number; height: number }) => void
      }
    }
  }
}

export interface CustomEditorProps extends EditorProps {
  className?: string
  onKeyDown?: () => void
  workspaceId?: string
  workflowId?: string | null
}

export function CustomEditor({
  className,
  onKeyDown,
  workspaceId,
  workflowId,
  ...props
}: CustomEditorProps) {
  const completionDisposableRef = useRef<IDisposable | null>(null)
  const hoverDisposableRef = useRef<IDisposable | null>(null)
  const tokenizerDisposableRef = useRef<IDisposable | null>(null)

  const handleEditorDidMount = async (
    editor: editor.IStandaloneCodeEditor,
    monaco: Monaco
  ) => {
    // Cleanup previous providers if they exist
    completionDisposableRef.current?.dispose()
    hoverDisposableRef.current?.dispose()
    tokenizerDisposableRef.current?.dispose()

    // Register a custom token provider for YAML
    monaco.languages.register({ id: "yaml-extended" })
    monaco.languages.setLanguageConfiguration("yaml-extended", yamlConf)
    monaco.languages.setMonarchTokensProvider("yaml-extended", yamlLanguage)

    monaco.languages.register({ id: "tracecat-dsl" })
    const { conf, lang } = constructDslLang()
    monaco.languages.setLanguageConfiguration("tracecat-dsl", conf)
    tokenizerDisposableRef.current = monaco.languages.setMonarchTokensProvider(
      "tracecat-dsl",
      lang
    )

    monaco.editor.defineTheme("myCustomTheme", {
      base: "vs",
      inherit: true,
      rules: [
        {
          token: "delimiter.expression",
          foreground: EverforestLightMed.AQUA,
          fontStyle: "bold",
        },
        {
          token: "expr.context.global",
          foreground: EverforestLightMed.STATUSLINE3_RED,
          fontStyle: "bold",
        },
        {
          token: "expr.context.local",
          foreground: EverforestLightMed.STATUSLINE3_RED,
          fontStyle: "bold",
        },
        {
          token: "expr.jsonpath",
          foreground: EverforestLightMed.FOREGROUND,
          fontStyle: "bold",
        },
        {
          token: "expr.parameter",
          foreground: EverforestLightMed.DARK_GREY,
        },
        {
          token: "expr.operator",
          foreground: EverforestLightMed.DARK_GREY,
        },
        {
          token: "expr.base",
          foreground: EverforestLightMed.FOREGROUND,
        },
        {
          token: "source",
          foreground: EverforestLightMed.BLUE,
        },
        {
          token: "expr.function.name",
          foreground: "65a30d",
          fontStyle: "bold",
        },
        // Overrides for YAML
        {
          token: "delimiter.bracket",
          foreground: "000000",
        },
        {
          token: "keyword",
          foreground: EverforestLightMed.STATUSLINE3_RED, // Changed to a brighter magenta
          fontStyle: "bold",
        },
        {
          token: "keyword.control",
          foreground: "0000FF",
        },
        {
          token: "keyword.operator",
          foreground: "00FF00",
        },
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
        "editor.findMatchBackground": "#88000030", // Background for matches
        "editor.findMatchHighlightBackground": "#88000015", // Background for other matches
        "editor.selectionHighlightBackground": "#88000015", // Background when selecting similar text
      },
    })
    monaco.editor.setTheme("myCustomTheme")

    completionDisposableRef.current =
      monaco.languages.registerCompletionItemProvider("yaml-extended", {
        triggerCharacters: [
          "$",
          "{",
          ".",
          " ",
          ...EXPR_CONTEXTS.map((c) => c[0]),
        ],
        provideCompletionItems: async (model, position, context, token) => {
          const wordUntilPos = model.getWordUntilPosition(position)
          const lineContent = model.getLineContent(position.lineNumber)
          const textUntilPos = lineContent.substring(0, position.column - 1)

          console.log("TEXT: ", textUntilPos)
          console.log("WORD: ", wordUntilPos)
          console.log("lineContent", lineContent)
          console.log("Trigger", context.triggerCharacter)

          const range: IRange = {
            startLineNumber: position.lineNumber,
            endLineNumber: position.lineNumber,
            startColumn: wordUntilPos.startColumn,
            endColumn: position.column,
          }

          // Use negative lookahead to ensure we don't match outside of expressions
          if (textUntilPos.match(INSIDE_EXPR_PATTERN)) {
            console.log("MATCH: inside expr")

            // 1. Check if specific context
            if (textUntilPos.match(ACTION_CONTEXT_PATTERN)) {
              console.log("MATCH: inside actions")

              if (textUntilPos.endsWith("ACTIONS.")) {
                if (workflowId && workspaceId) {
                  console.log("MATCH: get action completions")
                  return {
                    suggestions: await getActionCompletions(
                      monaco,
                      range,
                      workspaceId,
                      workflowId
                    ),
                  }
                } else {
                  console.log("Can't load action completions")
                }
              }
              if (textUntilPos.match(ACTION_REF_PATTERN)) {
                console.log("MATCH: action ref")
                return {
                  suggestions: [
                    {
                      label: "result",
                      kind: monaco.languages.CompletionItemKind.Property,
                      detail: "The successful result of the action",
                      insertText: "result",
                      range,
                    },
                    {
                      label: "error",
                      kind: monaco.languages.CompletionItemKind.Property,
                      detail: "The error message if the action failed",
                      insertText: "error",
                      range,
                    },
                  ],
                }
              }

              if (textUntilPos.match(ACTIONS_RES_ERR_PATTERN)) {
                console.log("MATCH: action res err")
                return { suggestions: [] }
              }

              return { suggestions: [] }
            }

            if (textUntilPos.endsWith("FN.") && workspaceId) {
              console.log("MATCH: inside fn")
              return {
                suggestions: await getFunctionSuggestions(
                  monaco,
                  range,
                  workspaceId
                ),
              }
            }

            if (textUntilPos.endsWith("ENV.")) {
              console.log("MATCH: inside env")
              return { suggestions: getEnvCompletions(range) }
            }

            if (textUntilPos.endsWith("INPUTS.")) {
              console.log("MATCH: inside inputs")
              return { suggestions: getInputCompletions(range) }
            }

            if (textUntilPos.endsWith("SECRETS.")) {
              console.log("MATCH: inside secrets")
              return { suggestions: getSecretCompletions(range) }
            }

            if (textUntilPos.endsWith("TRIGGER.")) {
              console.log("MATCH: inside trigger")
              return { suggestions: getTriggerCompletions(range) }
            }

            // 2. Check if general context
            const shouldPad = context.triggerCharacter === "{"
            return {
              suggestions: getContextSuggestions(monaco, range).map((s) => ({
                ...s,
                insertText: formatContextPadding(s.insertText, shouldPad),
                insertTextRules:
                  monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
                range,
              })),
            }
          }

          if (textUntilPos.endsWith("$")) {
            console.log("MATCH: starting $")
            return {
              suggestions: getExpressionCompletions(range),
            }
          }

          console.log("NO MATCH")
          return { suggestions: [] }
        },
      })

    // Register and store the hover provider
    hoverDisposableRef.current = monaco.languages.registerHoverProvider(
      "yaml-extended",
      {
        provideHover: async (model, position) => {
          if (!workspaceId) {
            return null
          }
          const wordAtPosition = model.getWordAtPosition(position)
          if (!wordAtPosition) return null

          const word = wordAtPosition.word
          const lineContent = model.getLineContent(position.lineNumber)

          // Check if we're in a FN context
          try {
            const functions = await editorListFunctions({ workspaceId })
            if (lineContent.includes("FN.") && functions) {
              const fn = functions.find((f) => f.name === word)
              if (fn) {
                return generateFunctionDocs(fn)
              }
            }
          } catch {
            console.log("Couldn't fetch function completions")
          }

          return null
        },
      }
    )

    // Get the suggest widget from the editor
    // This shows the details of the suggestion widget by default
    const suggestController = editor.getContribution(
      "editor.contrib.suggestController"
    ) as ISuggestController | null
    if (suggestController) {
      const widget = suggestController.widget
      if (widget?.value && widget.value._setDetailsVisible) {
        // This will default to visible details
        widget.value._setDetailsVisible(true)

        // Optionally set the widget size
        // if (widget.value._persistedSize) {
        //   widget.value._persistedSize.store({ width: 200, height: 256 })
        // }
      }
    }
  }

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      completionDisposableRef.current?.dispose()
      hoverDisposableRef.current?.dispose()
      tokenizerDisposableRef.current?.dispose()
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
          language="yaml-extended"
          options={{
            tabSize: 2,
            minimap: { enabled: false },
            scrollbar: {
              verticalScrollbarSize: 10,
              horizontalScrollbarSize: 10,
            },
            renderLineHighlight: "all",
            automaticLayout: true,
            fixedOverflowWidgets: true,
            bracketPairColorization: { enabled: false },
            ...props.options,
          }}
          wrapperProps={{ className: "editor-wrapper" }}
          {...props}
        />
      </div>
    </>
  )
}

export default CustomEditor
