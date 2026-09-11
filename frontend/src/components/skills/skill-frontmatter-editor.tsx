"use client"

import { CodeEditor } from "@/components/editor/codemirror/code-editor"
import { skillToolsExtension } from "@/components/editor/codemirror/skill-tools-extension"

/** YAML editor with picker-owned tools hidden and protected from text edits. */
export function SkillFrontmatterEditor({
  value,
  onChange,
}: {
  value: string
  onChange: (value: string) => void
}) {
  return (
    <CodeEditor
      value={value}
      onChange={onChange}
      language="yaml"
      wrapLongLines
      extensions={skillToolsExtension}
      className="[&_.cm-scroller]:max-h-64"
    />
  )
}
