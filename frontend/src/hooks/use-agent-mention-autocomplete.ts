"use client"

import type { KeyboardEvent, RefObject } from "react"
import { useCallback, useMemo, useState } from "react"
import type { AgentPresetReadMinimal } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { useAgentPresets } from "@/hooks/use-agent-presets"
import { useEntitlements } from "@/hooks/use-entitlements"

/** Maximum number of agent presets listed in the mention popover. */
const MAX_AGENT_MENTION_RESULTS = 8

/** Caret-anchored `@query` span that drives the mention popover. */
export interface AgentMentionToken {
  start: number
  end: number
  query: string
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
  /** Presets matching the active query, capped for display. */
  suggestions: AgentPresetReadMinimal[]
  activeIndex: number
  query: string
  isLoading: boolean
  /** Call from `onChange` with the textarea's next value and caret offset. */
  handleValueChange: (next: string, caret: number) => void
  /** Returns true when the popover consumed the key event. */
  handleKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => boolean
  selectPreset: (preset: AgentPresetReadMinimal) => void
  dismiss: () => void
}

type ActiveMention = AgentMentionToken & { activeIndex: number }

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

  const suggestions = useMemo(() => {
    if (!mention) {
      return []
    }
    const query = mention.query.toLowerCase()
    const matches = (presets ?? []).filter((preset) =>
      preset.name.toLowerCase().includes(query)
    )
    return matches.slice(0, MAX_AGENT_MENTION_RESULTS)
  }, [mention, presets])

  // Clamp on read so a shrinking suggestion list cannot strand the highlight.
  const activeIndex = mention
    ? Math.min(mention.activeIndex, Math.max(suggestions.length - 1, 0))
    : 0
  const isOpen = mentionsEnabled && mention !== undefined

  const dismiss = useCallback(() => setMention(undefined), [])

  const handleValueChange = useCallback(
    (next: string, caret: number) => {
      if (!mentionsEnabled) {
        setMention(undefined)
        return
      }
      const token = getAgentMentionToken(next, caret)
      if (!token) {
        setMention(undefined)
        return
      }
      setMention((current) => ({
        ...token,
        activeIndex: current?.activeIndex ?? 0,
      }))
    },
    [mentionsEnabled]
  )

  const selectPreset = useCallback(
    (preset: AgentPresetReadMinimal) => {
      if (!mention) {
        return
      }
      const token = formatAgentMentionToken(preset)
      const next = `${value.slice(0, mention.start)}${token}${value.slice(mention.end)}`
      const caret = mention.start + token.length
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
        if (suggestions.length === 0) {
          return true
        }
        const step = event.key === "ArrowDown" ? 1 : suggestions.length - 1
        setMention((current) =>
          current
            ? {
                ...current,
                activeIndex: (activeIndex + step) % suggestions.length,
              }
            : current
        )
        return true
      }

      if (event.key === "Enter") {
        const selected = suggestions[activeIndex]
        if (!selected) {
          return false
        }
        event.preventDefault()
        selectPreset(selected)
        return true
      }

      return false
    },
    [activeIndex, isOpen, selectPreset, suggestions]
  )

  return {
    isOpen,
    suggestions,
    activeIndex,
    query: mention?.query ?? "",
    isLoading: presetsIsLoading,
    handleValueChange,
    handleKeyDown,
    selectPreset,
    dismiss,
  }
}
