"use client"

import { codeBlock } from "@blocknote/code-block"
import {
  type DefaultReactSuggestionItem,
  DragHandleMenu,
  getDefaultReactSlashMenuItems,
  RemoveBlockItem,
  SideMenu,
  SideMenuController,
  SuggestionMenuController,
  useCreateBlockNote,
} from "@blocknote/react"
import { BlockNoteView } from "@blocknote/shadcn"

import * as Button from "@/components/ui/button"
import * as Card from "@/components/ui/card"
import * as DropdownMenu from "@/components/ui/dropdown-menu"
import * as Input from "@/components/ui/input"
import * as Popover from "@/components/ui/popover"
import * as Select from "@/components/ui/select"

import "@blocknote/core/fonts/inter.css"
import "@blocknote/shadcn/style.css"
import "./editor.css"

import { type BlockNoteEditor, filterSuggestionItems } from "@blocknote/core"
import { Trash2Icon } from "lucide-react"
import { useEffect, useRef, useState } from "react"

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
  const [isLoading, setIsLoading] = useState(false)
  const [hasLoaded, setHasLoaded] = useState(false)
  const loadIdRef = useRef(0)
  const isMountedRef = useRef(true)

  useEffect(() => {
    return () => {
      isMountedRef.current = false
    }
  }, [])

  // Creates a new editor instance.
  const editor = useCreateBlockNote({
    animations: false,
    codeBlock,
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
    if (initialContent === undefined || hasLoaded || isLoading) {
      return
    }

    loadIdRef.current += 1
    const currentLoadId = loadIdRef.current
    setIsLoading(true)

    ;(async () => {
      try {
        await loadInitialContent(editor, initialContent)
        if (!isMountedRef.current || loadIdRef.current !== currentLoadId) {
          return
        }
        setHasLoaded(true)
      } catch (error) {
        console.error("Failed to load initial content:", error)
      } finally {
        if (isMountedRef.current && loadIdRef.current === currentLoadId) {
          setIsLoading(false)
        }
      }
    })()
  }, [initialContent, editor, hasLoaded, isLoading])

  // Reset hasLoaded when content changes significantly
  useEffect(() => {
    setHasLoaded(false)
  }, [initialContent])

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
        <SideMenuController
          sideMenu={(props) => (
            <SideMenu
              {...props}
              dragHandleMenu={(props) => (
                <DragHandleMenu {...props}>
                  <RemoveBlockItem {...props}>
                    <div className="group flex w-full items-center">
                      <Trash2Icon className="mr-1 size-3 group-hover:text-red-500" />
                      <span className="group-hover:text-red-500">Delete</span>
                    </div>
                  </RemoveBlockItem>
                </DragHandleMenu>
              )}
            />
          )}
        />
      </BlockNoteView>
    </div>
  )
}

export function CaseCommentViewer({
  content,
  className,
}: {
  content: string
  className?: string
}) {
  const [isLoading, setIsLoading] = useState(false)
  const [hasLoaded, setHasLoaded] = useState(false)
  const loadIdRef = useRef(0)
  const isMountedRef = useRef(true)

  // Creates a new editor instance.
  const editor = useCreateBlockNote({
    animations: false,
    codeBlock,
  })

  useEffect(() => {
    return () => {
      isMountedRef.current = false
    }
  }, [])

  useEffect(() => {
    if (content === undefined || hasLoaded || isLoading) {
      return
    }

    loadIdRef.current += 1
    const currentLoadId = loadIdRef.current
    setIsLoading(true)

    ;(async () => {
      try {
        await loadInitialContent(editor, content)
        if (!isMountedRef.current || loadIdRef.current !== currentLoadId) {
          return
        }
        setHasLoaded(true)
      } catch (error) {
        console.error("Failed to load initial content:", error)
      } finally {
        if (isMountedRef.current && loadIdRef.current === currentLoadId) {
          setIsLoading(false)
        }
      }
    })()
  }, [content, editor, hasLoaded, isLoading])

  // Reset hasLoaded when content changes
  useEffect(() => {
    setHasLoaded(false)
  }, [content])

  // Renders the editor instance using a React component.
  return (
    <div className={cn("text-base p-0 m-0", className)}>
      <BlockNoteView
        editor={editor}
        theme="light"
        editable={false}
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
          padding: 0,
          margin: 0,
          fontSize: "14px",
        }}
      />
    </div>
  )
}

async function loadInitialContent(editor: BlockNoteEditor, content: string) {
  const blocks = await editor.tryParseMarkdownToBlocks(content)
  const spacedBlocks = getSpacedBlocks(blocks)
  editor.replaceBlocks(editor.document, spacedBlocks)
}
