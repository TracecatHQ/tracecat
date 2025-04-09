"use client"

import * as Button from "@/components/ui/button"
import * as Card from "@/components/ui/card"
import * as DropdownMenu from "@/components/ui/dropdown-menu"
import * as Input from "@/components/ui/input"
import * as Popover from "@/components/ui/popover"
import * as Select from "@/components/ui/select"

import "@blocknote/core/fonts/inter.css"

import {
  DefaultReactSuggestionItem,
  getDefaultReactSlashMenuItems,
  SuggestionMenuController,
  useCreateBlockNote,
} from "@blocknote/react"
import { BlockNoteView } from "@blocknote/shadcn"

import "@blocknote/shadcn/style.css"
import "./editor.css"

import { useEffect } from "react"
import { BlockNoteEditor, filterSuggestionItems } from "@blocknote/core"

import { getSpacedBlocks } from "@/lib/rich-text-editor"
import { cn } from "@/lib/utils"

const getCustomSlashMenuItems = (
  editor: BlockNoteEditor
): DefaultReactSuggestionItem[] =>
  getDefaultReactSlashMenuItems(editor).filter(
    (item) => item.group?.toLowerCase() !== "media"
  )
interface CaseDescriptionEditorProps {
  initialContent?: string
  onChange?: (value: string) => void
  className?: string
  onBlur?: () => void
}

export function CaseDescriptionEditor({
  initialContent,
  onChange,
  className,
  onBlur,
}: CaseDescriptionEditorProps) {
  // Creates a new editor instance.
  const editor = useCreateBlockNote({
    animations: false,
  })

  // Handle changes in the editor content
  const handleEditorChange = async () => {
    if (onChange) {
      // Convert the blocks back to markdown
      const blocks = editor.document
      const markdown = await editor.blocksToMarkdownLossy(blocks)
      onChange(markdown)
    }
  }

  useEffect(() => {
    const loadInitialContent = async () => {
      if (initialContent) {
        const blocks = await editor.tryParseMarkdownToBlocks(initialContent)
        const spacedBlocks = getSpacedBlocks(blocks)
        editor.replaceBlocks(editor.document, spacedBlocks)
      }
    }

    loadInitialContent()
  }, [initialContent, editor])

  // Renders the editor instance using a React component.
  return (
    <div className={cn("mx-0  py-2", className)} onBlur={onBlur}>
      <BlockNoteView
        editor={editor}
        onChange={handleEditorChange}
        theme="light"
        slashMenu={false}
        shadCNComponents={{
          Button,
          Input,
          Popover,
          DropdownMenu, // This is used for the drag handle dropdown menu
          Select,
          Card,
        }}
        style={{
          height: "100%",
        }}
      >
        <SuggestionMenuController
          triggerCharacter={"/"}
          // Replaces the default Slash Menu items with our custom ones.
          getItems={async (query) =>
            filterSuggestionItems(getCustomSlashMenuItems(editor), query)
          }
        />
      </BlockNoteView>
    </div>
  )
}
