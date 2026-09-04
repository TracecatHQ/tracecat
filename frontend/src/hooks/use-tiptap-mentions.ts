"use client"

import { TextSelection } from "@tiptap/pm/state"
import type { Editor } from "@tiptap/react"
import { useCallback, useEffect, useMemo, useState } from "react"
import {
  type MentionSourceConfig,
  type MentionSourceState,
  type MentionSuggestion,
  useMentionSuggestions,
} from "@/hooks/use-mentions"
import {
  getMentionToken,
  type MentionKind,
  mentionDisplayText,
} from "@/lib/mentions"
import type { CaretCoordinates } from "@/lib/textarea-caret"
import {
  buildAgentMentionHref,
  buildWorkflowMentionHref,
  commentMentionLeafText,
  findCommentMentionLinkRanges,
  nodeAllowsCommentMention,
  serializeTiptapComment,
  WORKFLOW_MENTION_URI_SCHEME,
} from "@/lib/tiptap-comment-mentions"

interface TiptapMentionSession {
  from: number
  to: number
  query: string
  kind: MentionKind
  activeIndex: number
  caret: CaretCoordinates
}

interface UseTiptapMentionsOptions {
  editor: Editor | null
  workspaceId: string
  agents?: MentionSourceConfig
  workflows?: MentionSourceConfig
}

/** Mention behavior exposed to a TipTap comment composer. */
export interface TiptapMentions {
  agents: MentionSourceState
  workflows: MentionSourceState
  isOpen: boolean
  locked: boolean
  kind: MentionKind | undefined
  sections: ReturnType<typeof useMentionSuggestions>["sections"]
  itemCount: number
  activeIndex: number
  caret: CaretCoordinates | undefined
  isLoading: boolean
  hasError: boolean
  selectSuggestion: (suggestion: MentionSuggestion) => void
  dismiss: () => void
  handleKeyDown: (event: KeyboardEvent) => boolean
  serialize: (markdown: string) => ReturnType<typeof serializeTiptapComment>
  reset: () => void
}

function measureCaret(editor: Editor, pos: number): CaretCoordinates {
  const caret = editor.view.coordsAtPos(pos)
  const wrapper = editor.view.dom.closest(".simple-editor-wrapper")
  const wrapperRect = (wrapper ?? editor.view.dom).getBoundingClientRect()
  return {
    top: caret.top - wrapperRect.top,
    left: caret.left - wrapperRect.left,
    height: Math.max(caret.bottom - caret.top, 1),
  }
}

function deleteMentionBeforeCaret(editor: Editor): boolean {
  const { selection, doc } = editor.state
  if (!selection.empty) {
    return false
  }
  const range = findCommentMentionLinkRanges(doc).find(
    (candidate) => candidate.to === selection.from
  )
  if (!range) {
    return false
  }
  let transaction = editor.state.tr.delete(range.from, range.to)
  transaction = transaction.setSelection(
    TextSelection.create(transaction.doc, range.from)
  )
  editor.view.dispatch(transaction)
  editor.view.focus()
  return true
}

/**
 * TipTap-native adapter for the existing agent/workflow comment mentions.
 *
 * Selected agents are link marks whose Markdown is the existing persisted
 * `mention://agent/...` token. Selected workflows use a transient link scheme
 * that is removed by `serialize` and returned as the API's `workflow_id`.
 */
export function useTiptapMentions({
  editor,
  workspaceId,
  agents: agentsConfig,
  workflows: workflowsConfig,
}: UseTiptapMentionsOptions): TiptapMentions {
  const [session, setSession] = useState<TiptapMentionSession | undefined>()
  const { agents, workflows, sections, locked, isLoading, hasError } =
    useMentionSuggestions({
      workspaceId,
      activeMention: session,
      agents: agentsConfig,
      workflows: workflowsConfig,
    })
  const items = useMemo(
    () => sections.flatMap((section) => section.items),
    [sections]
  )
  const activeIndex = session
    ? Math.min(session.activeIndex, Math.max(items.length - 1, 0))
    : 0
  const abandoned =
    session !== undefined &&
    !locked &&
    !hasError &&
    !isLoading &&
    /\s/.test(session.query) &&
    items.length === 0
  const isOpen = session !== undefined && !abandoned

  const dismiss = useCallback(() => setSession(undefined), [])

  const syncSession = useCallback(() => {
    if (!editor?.isFocused || !editor.state.selection.empty) {
      setSession(undefined)
      return
    }
    const { $from } = editor.state.selection
    const linkMark = editor.state.schema.marks.link
    if (
      !$from.parent.isTextblock ||
      !nodeAllowsCommentMention($from.parent, linkMark)
    ) {
      setSession(undefined)
      return
    }
    const text = $from.parent.textBetween(
      0,
      $from.parentOffset,
      "\n",
      commentMentionLeafText
    )
    const token = getMentionToken(text, text.length)
    if (!token) {
      setSession(undefined)
      return
    }
    const from = $from.start() + token.start
    const to = $from.start() + token.end
    const isInsideBoundMention = findCommentMentionLinkRanges(
      editor.state.doc
    ).some((range) => from >= range.from && from < range.to)
    const sourceState = token.kind === "agent" ? agents : workflows
    if (isInsideBoundMention || sourceState === "unavailable") {
      setSession(undefined)
      return
    }
    setSession((current) => ({
      from,
      to,
      query: token.query,
      kind: token.kind,
      activeIndex: current?.from === from ? current.activeIndex : 0,
      caret:
        current?.from === from ? current.caret : measureCaret(editor, from),
    }))
  }, [agents, editor, workflows])

  useEffect(() => {
    if (!editor) {
      setSession(undefined)
      return
    }
    const handleChange = () => syncSession()
    const handleBlur = () => setSession(undefined)
    editor.on("update", handleChange)
    editor.on("selectionUpdate", handleChange)
    editor.on("focus", handleChange)
    editor.on("blur", handleBlur)
    syncSession()
    return () => {
      editor.off("update", handleChange)
      editor.off("selectionUpdate", handleChange)
      editor.off("focus", handleChange)
      editor.off("blur", handleBlur)
    }
  }, [editor, syncSession])

  const selectSuggestion = useCallback(
    (suggestion: MentionSuggestion) => {
      if (!editor || !session) {
        return
      }
      const link = editor.state.schema.marks.link
      if (!link) {
        return
      }
      let transaction = editor.state.tr
      if (suggestion.kind === "workflow") {
        const existing = findCommentMentionLinkRanges(editor.state.doc)
          .filter((range) => range.href.startsWith(WORKFLOW_MENTION_URI_SCHEME))
          .reverse()
        for (const range of existing) {
          transaction = transaction.delete(range.from, range.to)
        }
      }
      const from = transaction.mapping.map(session.from)
      const to = transaction.mapping.map(session.to)
      const href =
        suggestion.kind === "agent"
          ? buildAgentMentionHref(suggestion.id)
          : buildWorkflowMentionHref(suggestion.id)
      const display = mentionDisplayText(suggestion.kind, suggestion.label)
      const mentionText = editor.state.schema.text(display, [
        link.create({ href }),
      ])
      transaction = transaction.replaceWith(from, to, mentionText)
      const cursor = from + mentionText.nodeSize
      transaction = transaction
        // Insert a genuinely unmarked spacer. `insertText` inherits the link
        // mark at the mention boundary and would serialize the label as
        // `[@Agent ](...)`, changing the existing wire token.
        .insert(cursor, editor.state.schema.text(" "))
        .setSelection(TextSelection.create(transaction.doc, cursor + 1))
        .setStoredMarks([])
      setSession(undefined)
      editor.view.dispatch(transaction)
      editor.view.focus()
    },
    [editor, session]
  )

  const handleKeyDown = useCallback(
    (event: KeyboardEvent): boolean => {
      if (event.isComposing) {
        return false
      }
      if (isOpen) {
        if (event.key === "Escape") {
          event.preventDefault()
          setSession(undefined)
          return true
        }
        if (items.length > 0) {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault()
            const step = event.key === "ArrowDown" ? 1 : items.length - 1
            setSession((current) =>
              current
                ? {
                    ...current,
                    activeIndex: (activeIndex + step) % items.length,
                  }
                : current
            )
            return true
          }
          if (
            event.key === "Enter" ||
            (event.key === "Tab" && !event.shiftKey)
          ) {
            event.preventDefault()
            const selected = items[activeIndex]
            if (selected) {
              selectSuggestion(selected)
            }
            return true
          }
        }
      }
      if (event.key === "Backspace" && editor) {
        if (deleteMentionBeforeCaret(editor)) {
          event.preventDefault()
          return true
        }
      }
      return false
    },
    [activeIndex, editor, isOpen, items, selectSuggestion]
  )

  const serialize = useCallback(
    (markdown: string) => {
      const workflowMentions = editor
        ? findCommentMentionLinkRanges(editor.state.doc).filter((range) =>
            range.href.startsWith(WORKFLOW_MENTION_URI_SCHEME)
          )
        : []
      return serializeTiptapComment(markdown, workflowMentions)
    },
    [editor]
  )

  return {
    agents,
    workflows,
    isOpen,
    locked,
    kind: session?.kind,
    sections,
    itemCount: items.length,
    activeIndex,
    caret: session?.caret,
    isLoading,
    hasError,
    selectSuggestion,
    dismiss,
    handleKeyDown,
    serialize,
    reset: dismiss,
  }
}
