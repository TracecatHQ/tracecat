import {
  EditorProps,
  Editor as ReactMonacoEditor,
  type Monaco,
} from "@monaco-editor/react"
import { editor } from "monaco-editor"

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
  }
  return (
    <div className={cn("h-36", className)}>
      <ReactMonacoEditor
        height="100%"
        loading={<CenteredSpinner />}
        onMount={handleEditorDidMount}
        theme="myCustomTheme"
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
