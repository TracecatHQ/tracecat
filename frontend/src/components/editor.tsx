import {
  EditorProps,
  Editor as ReactMonacoEditor,
  type Monaco,
} from "@monaco-editor/react"
import { editor, languages } from "monaco-editor"

import { cn } from "@/lib/utils"
import { CenteredSpinner } from "@/components/loading/spinner"

export function CustomEditor({
  className,
  onKeyDown,
  ...props
}: EditorProps & {
  className?: string
  onKeyDown?: () => void
}) {
  const handleEditorDidMount = (
    editor: editor.IStandaloneCodeEditor,
    monaco: Monaco
  ) => {
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
      ],
      colors: {
        // "editor.foreground": "#F8F8F8",
        // "editor.background": "#efefef",
        // "editorCursor.foreground": "#A7A7A7",
        "editor.lineHighlightBackground": "#e8f7ff",
        "editorLineNumber.foreground": "#5A5A5A",
        // "editor.selectionBackground": "#88000030",
        // "editor.inactiveSelectionBackground": "#88000015",
        // Add more color customizations here
      },
    })
    monaco.editor.setTheme("myCustomTheme")

    // Register for yaml language
    monaco.languages.registerCompletionItemProvider("yaml", {
      triggerCharacters: ["$", "{", "{"],
      provideCompletionItems: (model, position) => {
        const wordUntilPosition = model.getWordUntilPosition(position)
        const textUntilPosition = model.getValueInRange({
          startLineNumber: position.lineNumber,
          startColumn: 1,
          endLineNumber: position.lineNumber,
          endColumn: position.column,
        })

        console.group("Completion Provider")
        console.log("Triggered by character at position:", position)
        console.log("Text until position:", textUntilPosition)

        const shouldShowSuggestions = textUntilPosition.endsWith("${{")

        console.log("Should show suggestions:", shouldShowSuggestions)
        console.groupEnd()

        if (!shouldShowSuggestions) {
          return { suggestions: [] }
        }

        const suggestions: languages.CompletionItem[] = [
          {
            label: "ACTIONS",
            kind: monaco.languages.CompletionItemKind.Keyword,
            insertText: "ACTIONS",
            documentation: "Insert ACTIONS reference",
            range: {
              startLineNumber: position.lineNumber,
              endLineNumber: position.lineNumber,
              startColumn: wordUntilPosition.startColumn,
              endColumn: position.column,
            },
          },
          {
            label: "FN",
            kind: monaco.languages.CompletionItemKind.Function,
            insertText: "FN",
            documentation: "Insert Function reference",
            range: {
              startLineNumber: position.lineNumber,
              endLineNumber: position.lineNumber,
              startColumn: wordUntilPosition.startColumn,
              endColumn: position.column,
            },
          },
        ]

        return {
          suggestions: suggestions,
        }
      },
    })
  }
  return (
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
          ...props.options,
        }}
        // We're using a custom tailwind class to achieve rounded corners
        wrapperProps={{ className: "editor-wrapper" }}
        {...props}
      />
    </div>
  )
}
