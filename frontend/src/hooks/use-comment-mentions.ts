"use client"

import type { KeyboardEvent, RefObject } from "react"
import { useCallback, useMemo, useState } from "react"
import type { AgentPresetReadMinimal } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { useAgentPresets } from "@/hooks/use-agent-presets"
import { useEntitlements } from "@/hooks/use-entitlements"
import {
  applyMentionInsertion,
  applyMentionRemoval,
  diffTextSplice,
  findMentionEndingAt,
  getAgentMentionToken,
  type MentionEdit,
  type MentionRange,
  remapMentions,
  serializeMentions,
  type TextSplice,
} from "@/lib/comment-mentions"
import {
  type CaretCoordinates,
  getTextareaCaretCoordinates,
} from "@/lib/textarea-caret"

/** Maximum number of agent presets listed in the mention popover. */
const MAX_AGENT_MENTION_RESULTS = 8

/**
 * Mention sources the popover can group by. Only `agents` is populated today;
 * the rest are declared so later sources slot in without reshaping the model.
 */
export type MentionSectionKey = "agents" | "users" | "cases" | "tables"

export interface MentionSuggestion {
  /** Identifier of the mention target, e.g. the agent preset id. */
  id: string
  label: string
}

export interface MentionSection {
  section: MentionSectionKey
  label: string
  items: MentionSuggestion[]
}

export interface UseCommentMentionsOptions {
  workspaceId: string
  textareaRef: RefObject<HTMLTextAreaElement | null>
  /** Current display text; read at edit time so splices see the old value. */
  getText: () => string
  /** Writes display text back to the owning form. */
  setText: (next: string) => void
}

export interface CommentMentions {
  /** Mention ranges into the display text, for the overlay and serializer. */
  ranges: MentionRange[]
  /** True when the popover should be rendered. */
  isOpen: boolean
  /** Grouped suggestions; sections with no items are omitted. */
  sections: MentionSection[]
  /** Total selectable items across every section. */
  itemCount: number
  /** Index into the flattened item list, spanning sections. */
  activeIndex: number
  /** Position of the `@` trigger, measured once per mention session. */
  caret: CaretCoordinates | undefined
  isLoading: boolean
  selectSuggestion: (suggestion: MentionSuggestion) => void
  dismiss: () => void
  /** Call with the new value and caret BEFORE handing the change to the form. */
  handleTextChange: (next: string, caret: number) => void
  /** Call when only the selection moved (textarea `onSelect`). */
  handleSelectionChange: () => void
  /** Returns true when the mention layer consumed the key event. */
  handleKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => boolean
  /** Remap ranges across a programmatic edit, e.g. an image-paste insertion. */
  applySplice: (splice: TextSplice) => void
  /** Convert display text into the wire value sent to the API. */
  serialize: (text: string) => string
  /** Clear all mention state, e.g. after a successful submit. */
  reset: () => void
}

type ActiveSession = {
  start: number
  end: number
  query: string
  activeIndex: number
  caret: CaretCoordinates
}

/**
 * Mentions in a plain comment `<Textarea>`: `@`-autocomplete for agent presets
 * plus the display-value mapping that turns highlighted `@Label` display text
 * into wire tokens on submit.
 *
 * Gated on the `agent:execute` scope plus the `agent_addons` and `case_addons`
 * entitlements; when gated the popover never opens and `@` behaves as plain
 * text.
 */
export function useCommentMentions({
  workspaceId,
  textareaRef,
  getText,
  setText,
}: UseCommentMentionsOptions): CommentMentions {
  const canExecuteAgents = useScopeCheck("agent:execute")
  const { hasEntitlement } = useEntitlements()
  const mentionsEnabled =
    canExecuteAgents === true &&
    hasEntitlement("agent_addons") &&
    hasEntitlement("case_addons")

  const { presets, presetsIsLoading } = useAgentPresets(workspaceId, {
    enabled: mentionsEnabled,
  })
  const [ranges, setRanges] = useState<MentionRange[]>([])
  const [session, setSession] = useState<ActiveSession | undefined>(undefined)

  const sections = useMemo<MentionSection[]>(() => {
    if (!session) {
      return []
    }
    const query = session.query.toLowerCase()
    const items = (presets ?? [])
      .filter((preset) => preset.name.toLowerCase().includes(query))
      .slice(0, MAX_AGENT_MENTION_RESULTS)
      .map((preset: AgentPresetReadMinimal) => ({
        id: preset.id,
        label: preset.name,
      }))
    if (items.length === 0) {
      return []
    }
    return [{ section: "agents", label: "Agents", items }]
  }, [session, presets])

  const items = useMemo(
    () => sections.flatMap((section) => section.items),
    [sections]
  )

  // Clamp on read so a shrinking suggestion list cannot strand the highlight.
  const activeIndex = session
    ? Math.min(session.activeIndex, Math.max(items.length - 1, 0))
    : 0
  const isOpen = mentionsEnabled && session !== undefined

  const dismiss = useCallback(() => setSession(undefined), [])

  const sessionStart = session?.start
  const sessionCaret = session?.caret

  /** Open, move, or close the mention session for the given text and caret. */
  const syncSession = useCallback(
    (text: string, caret: number) => {
      const node = textareaRef.current
      if (!mentionsEnabled || !node) {
        setSession(undefined)
        return
      }
      const token = getAgentMentionToken(text, caret)
      if (!token) {
        setSession(undefined)
        return
      }
      // The anchor is pinned to the `@` for the life of one mention session, so
      // the popover holds still while the query grows. Measuring only when the
      // session starts also keeps this off the per-keystroke path.
      const pinned = sessionStart === token.start ? sessionCaret : undefined
      const coordinates =
        pinned ?? getTextareaCaretCoordinates(node, token.start)
      setSession((current) => ({
        ...token,
        caret: coordinates,
        activeIndex: pinned ? (current?.activeIndex ?? 0) : 0,
      }))
    },
    [mentionsEnabled, sessionCaret, sessionStart, textareaRef]
  )

  const handleSelectionChange = useCallback(() => {
    const node = textareaRef.current
    syncSession(node?.value ?? "", node?.selectionStart ?? 0)
  }, [syncSession, textareaRef])

  const handleTextChange = useCallback(
    (next: string, caret: number) => {
      setRanges((current) =>
        remapMentions(current, diffTextSplice(getText(), next, caret))
      )
      syncSession(next, caret)
    },
    [getText, syncSession]
  )

  const applySplice = useCallback((splice: TextSplice) => {
    setRanges((current) => remapMentions(current, splice))
  }, [])

  /** Write a pure edit back to the form, then restore focus and the caret. */
  const commitEdit = useCallback(
    (edit: MentionEdit) => {
      setRanges(edit.mentions)
      setText(edit.text)
      requestAnimationFrame(() => {
        const node = textareaRef.current
        if (!node) {
          return
        }
        node.focus()
        node.setSelectionRange(edit.caret, edit.caret)
      })
    },
    [setText, textareaRef]
  )

  const selectSuggestion = useCallback(
    (suggestion: MentionSuggestion) => {
      if (!session) {
        return
      }
      setSession(undefined)
      commitEdit(
        applyMentionInsertion(getText(), ranges, session, {
          label: suggestion.label,
          targetId: suggestion.id,
        })
      )
    },
    [commitEdit, getText, ranges, session]
  )

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (isOpen) {
        if (event.key === "Escape") {
          event.preventDefault()
          setSession(undefined)
          return true
        }

        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault()
          if (items.length === 0) {
            return true
          }
          const step = event.key === "ArrowDown" ? 1 : items.length - 1
          setSession((current) =>
            current
              ? { ...current, activeIndex: (activeIndex + step) % items.length }
              : current
          )
          return true
        }

        if (event.key === "Enter" || (event.key === "Tab" && !event.shiftKey)) {
          // Swallow Enter and Tab while open so they select instead of
          // submitting, adding a newline, or moving focus. Shift+Tab falls
          // through to native focus handling.
          event.preventDefault()
          const selected = items[activeIndex]
          if (selected) {
            selectSuggestion(selected)
          }
          return true
        }
      }

      // Backspace just after a mention removes the whole mention at once.
      if (event.key === "Backspace") {
        const node = event.currentTarget
        if (node.selectionStart !== node.selectionEnd) {
          return false
        }
        const mention = findMentionEndingAt(ranges, node.selectionStart)
        if (!mention) {
          return false
        }
        event.preventDefault()
        commitEdit(applyMentionRemoval(getText(), ranges, mention))
        return true
      }

      return false
    },
    [activeIndex, commitEdit, getText, isOpen, items, ranges, selectSuggestion]
  )

  const serialize = useCallback(
    (text: string) => serializeMentions(text, ranges),
    [ranges]
  )

  const reset = useCallback(() => {
    setRanges([])
    setSession(undefined)
  }, [])

  return {
    ranges,
    isOpen,
    sections,
    itemCount: items.length,
    activeIndex,
    caret: session?.caret,
    isLoading: presetsIsLoading,
    selectSuggestion,
    dismiss,
    handleTextChange,
    handleSelectionChange,
    handleKeyDown,
    applySplice,
    serialize,
    reset,
  }
}
