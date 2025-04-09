"use client"

import * as Button from "@/components/ui/button"
import * as Card from "@/components/ui/card"
import * as DropdownMenu from "@/components/ui/dropdown-menu"
import * as Input from "@/components/ui/input"
import * as Popover from "@/components/ui/popover"
import * as Select from "@/components/ui/select"

import "@blocknote/core/fonts/inter.css"

import { useCreateBlockNote } from "@blocknote/react"
import { BlockNoteView } from "@blocknote/shadcn"

import "@blocknote/shadcn/style.css"
import "./editor.css"

import { useEffect } from "react"

import { cn } from "@/lib/utils"

interface CaseDescriptionEditorProps {
  initialContent?: string
  onChange?: (value: string) => void
  className?: string
}

export function CaseDescriptionEditor({
  initialContent,
  onChange,
  className,
}: CaseDescriptionEditorProps) {
  // Creates a new editor instance.
  const editor = useCreateBlockNote({
    animations: false,
  })

  // Handle changes in the editor content
  const handleEditorChange = async () => {
    if (onChange) {
      // Convert the blocks back to markdown
      const markdown = await editor.blocksToMarkdownLossy(editor.document)
      onChange(markdown)
    }
  }

  useEffect(() => {
    const loadInitialContent = async () => {
      if (initialContent) {
        const blocks = await editor.tryParseMarkdownToBlocks(initialContent)
        editor.replaceBlocks(editor.document, blocks)
      }
    }

    loadInitialContent()
  }, [initialContent, editor])

  // Renders the editor instance using a React component.
  return (
    <div className={cn("mx-0  py-2", className)}>
      <BlockNoteView
        editor={editor}
        onChange={handleEditorChange}
        theme="light"
        shadCNComponents={{
          Button,
          Input,
          Popover,
          DropdownMenu, // This is used
          Select,
          Card,
        }}
        style={{
          height: "100%",
        }}
        sideMenu={true}
      />
    </div>
  )
}
