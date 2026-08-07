"use client"

import type { KeyboardEvent, RefObject } from "react"
import { useCallback, useMemo, useState } from "react"
import type { AgentPresetReadMinimal } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { useAgentPresets } from "@/hooks/use-agent-presets"
import { useEntitlements } from "@/hooks/use-entitlements"
import {
  type CaretCoordinates,
  getTextareaCaretCoordinates,
} from "@/lib/textarea-caret"

/** Maximum number of agent presets listed in the mention popover. */
const MAX_AGENT_MENTION_RESULTS = 8

/** Caret-anchored `@query` span that drives the mention popover. */
export interface AgentMentionToken {
  start: number
  end: number
  query: string
}

/**
 * Mention sources the popover can group by. Only `agents` is populated today;
 * the rest are declared so later sources slot in without reshaping the model.
 */
export type MentionSectionKey = "agents" | "users" | "cases" | "tables"

export interface MentionSuggestion {
  id: string
  label: string
  /** Markdown token inserted in place of the `@query`. */
  token: string
}

export interface MentionSection {
  section: MentionSectionKey
  label: string
  items: MentionSuggestion[]
}

/**
 * Locate the `@query` token immediately before the caret.
 *
 * The `@` must sit at the start of the text or directly after whitespace, and
 * any whitespace inside the query dismisses the token.
 */
export function getAgentMentionToken(
  text: string,
  caret: number
): AgentMentionToken | undefined {
  const beforeCaret = text.slice(0, caret)
  const atIndex = beforeCaret.lastIndexOf("@")
  if (atIndex < 0) {
    return undefined
  }

  const priorChar = atIndex === 0 ? " " : (beforeCaret[atIndex - 1] ?? " ")
  if (priorChar.trim() !== "") {
    return undefined
  }

  const query = beforeCaret.slice(atIndex + 1)
  if (/\s/.test(query)) {
    return undefined
  }

  return {
    start: atIndex,
    end: caret,
    query,
  }
}

/**
 * Render an agent preset as a mention token.
 *
 * The `[@Name](mention://agent/<preset_id>)` shape is a shared contract with
 * the comment renderer, so keep it byte-for-byte stable.
 */
export function formatAgentMentionToken(preset: {
  id: string
  name: string
}): string {
  return `[@${preset.name}](mention://agent/${preset.id})`
}

export interface UseAgentMentionAutocompleteOptions {
  workspaceId: string
  /** Current textarea value. */
  value: string
  /** Applies the value produced by selecting a mention. */
  onValueChange: (next: string) => void
  textareaRef: RefObject<HTMLTextAreaElement | null>
  /** Set to false to keep the popover closed regardless of permissions. */
  enabled?: boolean
}

export interface AgentMentionAutocomplete {
  /** True when the popover should be rendered. */
  isOpen: boolean
  /** Grouped suggestions; sections with no items are omitted. */
  sections: MentionSection[]
  /** Total selectable items across every section. */
  itemCount: number
  /** Index into the flattened item list, spanning sections. */
  activeIndex: number
  /** Caret position within the textarea, used to anchor the popover. */
  caret: CaretCoordinates | undefined
  query: string
  isLoading: boolean
  /** Call whenever the value or the selection moves. */
  handleCaretChange: () => void
  /** Returns true when the popover consumed the key event. */
  handleKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => boolean
  selectSuggestion: (suggestion: MentionSuggestion) => void
  dismiss: () => void
}

type ActiveMention = AgentMentionToken & {
  activeIndex: number
  caret: CaretCoordinates
}

/**
 * `@`-autocomplete for agent presets in a plain `<Textarea>`.
 *
 * Gated on the `agent:execute` scope plus the `agent_addons` and `case_addons`
 * entitlements; when gated the popover never opens and `@` behaves as plain
 * text.
 */
export function useAgentMentionAutocomplete({
  workspaceId,
  value,
  onValueChange,
  textareaRef,
  enabled = true,
}: UseAgentMentionAutocompleteOptions): AgentMentionAutocomplete {
  const canExecuteAgents = useScopeCheck("agent:execute")
  const { hasEntitlement } = useEntitlements()
  const mentionsEnabled =
    enabled &&
    canExecuteAgents === true &&
    hasEntitlement("agent_addons") &&
    hasEntitlement("case_addons")

  const { presets, presetsIsLoading } = useAgentPresets(workspaceId, {
    enabled: mentionsEnabled,
  })
  const [mention, setMention] = useState<ActiveMention | undefined>(undefined)

  const sections = useMemo<MentionSection[]>(() => {
    if (!mention) {
      return []
    }
    const query = mention.query.toLowerCase()
    const items = (presets ?? [])
      .filter((preset) => preset.name.toLowerCase().includes(query))
      .slice(0, MAX_AGENT_MENTION_RESULTS)
      .map((preset: AgentPresetReadMinimal) => ({
        id: preset.id,
        label: preset.name,
        token: formatAgentMentionToken(preset),
      }))
    if (items.length === 0) {
      return []
    }
    return [{ section: "agents", label: "Agents", items }]
  }, [mention, presets])

  const items = useMemo(
    () => sections.flatMap((section) => section.items),
    [sections]
  )

  // Clamp on read so a shrinking suggestion list cannot strand the highlight.
  const activeIndex = mention
    ? Math.min(mention.activeIndex, Math.max(items.length - 1, 0))
    : 0
  const isOpen = mentionsEnabled && mention !== undefined

  const dismiss = useCallback(() => setMention(undefined), [])

  const handleCaretChange = useCallback(() => {
    const node = textareaRef.current
    if (!mentionsEnabled || !node) {
      setMention(undefined)
      return
    }
    const token = getAgentMentionToken(
      node.value,
      node.selectionStart ?? node.value.length
    )
    if (!token) {
      setMention(undefined)
      return
    }
    const caret = getTextareaCaretCoordinates(node, token.end)
    setMention((current) => ({
      ...token,
      caret,
      activeIndex: current?.activeIndex ?? 0,
    }))
  }, [mentionsEnabled, textareaRef])

  const selectSuggestion = useCallback(
    (suggestion: MentionSuggestion) => {
      if (!mention) {
        return
      }
      const next = `${value.slice(0, mention.start)}${suggestion.token}${value.slice(mention.end)}`
      const caret = mention.start + suggestion.token.length
      setMention(undefined)
      onValueChange(next)
      requestAnimationFrame(() => {
        const node = textareaRef.current
        if (!node) {
          return
        }
        node.focus()
        node.setSelectionRange(caret, caret)
      })
    },
    [mention, onValueChange, textareaRef, value]
  )

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (!isOpen) {
        return false
      }

      if (event.key === "Escape") {
        event.preventDefault()
        setMention(undefined)
        return true
      }

      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault()
        if (items.length === 0) {
          return true
        }
        const step = event.key === "ArrowDown" ? 1 : items.length - 1
        setMention((current) =>
          current
            ? { ...current, activeIndex: (activeIndex + step) % items.length }
            : current
        )
        return true
      }

      if (event.key === "Enter") {
        // Swallow Enter while open so it neither submits nor adds a newline.
        event.preventDefault()
        const selected = items[activeIndex]
        if (selected) {
          selectSuggestion(selected)
        }
        return true
      }

      return false
    },
    [activeIndex, isOpen, items, selectSuggestion]
  )

  return {
    isOpen,
    sections,
    itemCount: items.length,
    activeIndex,
    caret: mention?.caret,
    query: mention?.query ?? "",
    isLoading: presetsIsLoading,
    handleCaretChange,
    handleKeyDown,
    selectSuggestion,
    dismiss,
  }
}
