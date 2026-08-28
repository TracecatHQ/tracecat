"use client"

import type { KeyboardEvent, RefObject } from "react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { AgentPresetReadMinimal } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { useAgentPresets } from "@/hooks/use-agent-presets"
import { useCommentWorkflows } from "@/hooks/use-comment-workflows"
import { type EntitlementKey, useEntitlements } from "@/hooks/use-entitlements"
import {
  applyMentionInsertion,
  applyMentionRemoval,
  diffTextSplice,
  findMentionEndingAt,
  findWorkflowMention,
  getMentionToken,
  type MentionEdit,
  type MentionKind,
  type MentionRange,
  remapMentions,
  serializeMentions,
  type TextSplice,
} from "@/lib/mentions"
import {
  type CaretCoordinates,
  getTextareaCaretCoordinates,
} from "@/lib/textarea-caret"

/** Maximum number of rows listed in the mention popover. */
const MAX_MENTION_RESULTS = 8

/**
 * Mention sources the popover can group by. `agents` and `workflows` are
 * populated today; the rest are declared so later sources slot in without
 * reshaping the model.
 */
export type MentionSectionKey =
  | "agents"
  | "workflows"
  | "users"
  | "cases"
  | "tables"

/**
 * How a mention source behaves on this surface.
 *
 * `unavailable` leaves the trigger character as plain text; `locked` opens the
 * popover on an Enterprise upsell row; `enabled` lists real suggestions.
 */
export type MentionSourceState = "unavailable" | "locked" | "enabled"

/** Entitlements an org needs for a source; missing any renders the lock row. */
export interface MentionSourceConfig {
  entitlements: EntitlementKey[]
  /**
   * The composer holds at most one mention of this kind, so picking a second
   * target replaces the first. True for workflows in a comment, which runs
   * one, and for agents in chat, whose session owns one preset.
   */
  single?: boolean
}

/** One selectable row in the mention popover. */
export interface MentionSuggestion {
  /** Identifier of the mention target, e.g. the agent preset or workflow id. */
  id: string
  kind: MentionKind
  label: string
  /** Secondary text after the label, e.g. a workflow alias or folder path. */
  hint?: string
}

/** A group of suggestions rendered under a single popover heading. */
export interface MentionSection {
  section: MentionSectionKey
  label: string
  items: MentionSuggestion[]
}

/**
 * Wiring the hook needs from the composer that owns the textarea. The composer
 * keeps the text in its own form state, so the hook reads and writes it through
 * these callbacks rather than holding a copy.
 */
export interface UseMentionsOptions {
  workspaceId: string
  textareaRef: RefObject<HTMLTextAreaElement | null>
  /** Current display text; read at edit time so splices see the old value. */
  getText: () => string
  /** Writes display text back to the owning form. */
  setText: (next: string) => void
  /** Omit a source to leave its trigger as plain text on this surface. */
  agents?: MentionSourceConfig
  /** Omit a source to leave its trigger as plain text on this surface. */
  workflows?: MentionSourceConfig
}

/**
 * Everything the composer needs to render and drive mentions: popover state for
 * the suggestion list, the ranges the highlight overlay paints, event handlers
 * to forward from the textarea, and the submit-time serializer.
 */
export interface Mentions {
  /** Mention ranges into the display text, for the overlay and serializer. */
  ranges: MentionRange[]
  /** How `@` behaves for this user on this surface. */
  agents: MentionSourceState
  /** How `/` behaves for this user on this surface. */
  workflows: MentionSourceState
  /** True when the popover should be rendered. */
  isOpen: boolean
  /** True when the open session's source is entitlement-locked. */
  locked: boolean
  /** Which trigger opened the popover, for its copy. */
  kind: MentionKind | undefined
  /** Grouped suggestions; sections with no items are omitted. */
  sections: MentionSection[]
  /** Total selectable items across every section. */
  itemCount: number
  /** Index into the flattened item list, spanning sections. */
  activeIndex: number
  /** Position of the trigger character, measured once per mention session. */
  caret: CaretCoordinates | undefined
  isLoading: boolean
  /** True when the open source's lookup failed, so it is not simply empty. */
  hasError: boolean
  /** Id of the workflow picked with `/`, or null when there is none. */
  workflowId: string | null
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
  kind: MentionKind
  activeIndex: number
  caret: CaretCoordinates
}

/**
 * Resolve a source's three-state gating.
 *
 * A source the surface never offers, or one the user lacks the scopes for, is
 * `unavailable`: a permissions problem should not surface as an upsell. A
 * scoped user missing an entitlement is `locked`, so the trigger still opens
 * and advertises the feature.
 *
 * `hasEntitlement` also answers false while its query is loading or errored,
 * so an unresolved entitlement stays `unavailable` rather than telling a
 * paying org that the feature it already has is Enterprise-only.
 */
function resolveSourceState(
  config: MentionSourceConfig | undefined,
  hasScopes: boolean,
  entitlementsKnown: boolean,
  hasEntitlement: (key: EntitlementKey) => boolean
): MentionSourceState {
  if (!config || !hasScopes || !entitlementsKnown) {
    return "unavailable"
  }
  return config.entitlements.every(hasEntitlement) ? "enabled" : "locked"
}

/**
 * Mentions in a plain `<textarea>` composer: `@`-autocomplete for agent
 * presets, `/`-autocomplete for workflows to run, plus the display-value
 * mapping that turns highlighted display text into the wire value on submit.
 *
 * Scopes are intrinsic to a source, so they are checked here: `agent:execute`
 * plus `agent:read` for agents, `workflow:execute` plus `workflow:read` for
 * workflows. Entitlements vary by surface and are passed in per source; a
 * source the caller omits leaves its trigger character as plain text.
 *
 * While the popover is open with no rows to pick — including the locked
 * state — Enter, Tab, and the arrow keys fall through to the textarea so the
 * user can still type a newline or submit.
 */
export function useMentions({
  workspaceId,
  textareaRef,
  getText,
  setText,
  agents: agentsConfig,
  workflows: workflowsConfig,
}: UseMentionsOptions): Mentions {
  // `agent:read` is required as well as `agent:execute`: the suggestion list
  // comes from the preset-list endpoint, which is guarded by `agent:read`.
  // Without it the request 403s and the popover would claim there are no
  // agents. Mirrors the workspace chat gate.
  const canUseAgents = useScopeCheck(
    undefined,
    ["agent:execute", "agent:read"],
    { all: true }
  )
  // `workflow:read` is required as well as `workflow:execute`: the suggestion
  // list comes from the workflow and folder list endpoints, which are guarded
  // by `workflow:read`. Without it the requests 403 and the popover would claim
  // there are no workflows. `workflow:execute` mirrors the API's check on
  // workflow-backed comments.
  const canExecuteWorkflows = useScopeCheck(
    undefined,
    ["workflow:execute", "workflow:read"],
    { all: true }
  )
  const { hasEntitlement, hasEntitlementData } = useEntitlements()
  const agents = resolveSourceState(
    agentsConfig,
    canUseAgents === true,
    hasEntitlementData,
    hasEntitlement
  )
  const workflows = resolveSourceState(
    workflowsConfig,
    canExecuteWorkflows === true,
    hasEntitlementData,
    hasEntitlement
  )

  const { presets, presetsIsLoading, presetsError } = useAgentPresets(
    workspaceId,
    { enabled: agents === "enabled" }
  )
  const { items: workflowItems, isLoading: workflowsIsLoading } =
    useCommentWorkflows(workspaceId, workflows === "enabled")
  const [ranges, setRanges] = useState<MentionRange[]>([])
  const [session, setSession] = useState<ActiveSession | undefined>(undefined)

  let sessionState: MentionSourceState | undefined
  if (session) {
    sessionState = session.kind === "agent" ? agents : workflows
  }
  const locked = sessionState === "locked"

  const sections = useMemo<MentionSection[]>(() => {
    if (!session || sessionState === "locked") {
      return []
    }
    const query = session.query.toLowerCase()
    if (session.kind === "workflow") {
      const items = workflowItems
        .filter(
          (workflow) =>
            workflow.title.toLowerCase().includes(query) ||
            (workflow.alias?.toLowerCase().includes(query) ?? false)
        )
        .slice(0, MAX_MENTION_RESULTS)
        .map(
          (workflow): MentionSuggestion => ({
            id: workflow.id,
            kind: "workflow",
            label: workflow.title,
            hint: workflow.alias || workflow.folderPath || undefined,
          })
        )
      if (items.length === 0) {
        return []
      }
      return [{ section: "workflows", label: "Workflows", items }]
    }
    const items = (presets ?? [])
      .filter((preset) => preset.name.toLowerCase().includes(query))
      .slice(0, MAX_MENTION_RESULTS)
      .map(
        (preset: AgentPresetReadMinimal): MentionSuggestion => ({
          id: preset.id,
          kind: "agent",
          // Names are not unique -- only the slug is -- so show it the way the
          // workflow rows show an alias. Two presets sharing a name would
          // otherwise render as identical rows bound to different agents.
          hint: preset.slug,
          label: preset.name,
        })
      )
    if (items.length === 0) {
      return []
    }
    return [{ section: "agents", label: "Agents", items }]
  }, [session, sessionState, presets, workflowItems])

  const items = useMemo(
    () => sections.flatMap((section) => section.items),
    [sections]
  )

  // Clamp on read so a shrinking suggestion list cannot strand the highlight.
  const activeIndex = session
    ? Math.min(session.activeIndex, Math.max(items.length - 1, 0))
    : 0

  // A locked source is never fetched, so it reports neither spinner nor error.
  let isLoading = false
  if (!locked && session?.kind === "agent") {
    isLoading = presetsIsLoading
  } else if (!locked && session?.kind === "workflow") {
    isLoading = workflowsIsLoading
  }

  // A failed lookup returns no rows, which would otherwise read as "no agents
  // found" and hide the fact that the request needs retrying.
  const hasError = !locked && session?.kind === "agent" && Boolean(presetsError)

  // A query may span spaces so a multi-word name can be typed out, but once it
  // matches nothing the user is writing prose after a stray trigger. Release
  // the popover then, and take it back if they edit their way to a match
  // again. Derived rather than stored so no keystroke writes state twice.
  //
  // Only an enabled source can be abandoned: a locked or failed one is empty
  // for its own reason, and its row is the thing worth reading.
  const abandoned =
    session !== undefined &&
    !locked &&
    !hasError &&
    !isLoading &&
    /\s/.test(session.query) &&
    items.length === 0
  const isOpen = session !== undefined && !abandoned

  const dismiss = useCallback(() => setSession(undefined), [])

  const sessionStart = session?.start
  const sessionCaret = session?.caret

  /** Open, move, or close the mention session for the given text and caret. */
  const syncSession = useCallback(
    (text: string, caret: number) => {
      const node = textareaRef.current
      if (!node) {
        setSession(undefined)
        return
      }
      const token = getMentionToken(text, caret)
      if (!token) {
        setSession(undefined)
        return
      }
      const state = token.kind === "agent" ? agents : workflows
      if (state === "unavailable") {
        setSession(undefined)
        return
      }
      // The anchor is pinned to the trigger for the life of one mention
      // session, so the popover holds still while the query grows. Measuring
      // only when the session starts also keeps this off the per-keystroke
      // path.
      const pinned = sessionStart === token.start ? sessionCaret : undefined
      const coordinates =
        pinned ?? getTextareaCaretCoordinates(node, token.start)
      setSession((current) => ({
        ...token,
        caret: coordinates,
        activeIndex: pinned ? (current?.activeIndex ?? 0) : 0,
      }))
    },
    [agents, workflows, sessionCaret, sessionStart, textareaRef]
  )

  // Scopes and entitlements resolve after mount, so a trigger typed during the
  // cold load is discarded as `unavailable` and nothing would look at it again
  // until the next keystroke -- a user who types `@` and waits would see
  // neither the list nor the Enterprise row. Rescan once access settles. The
  // key guard keeps `syncSession` changing identity from re-entering this.
  const lastAccessStateRef = useRef(`${agents}:${workflows}`)
  useEffect(() => {
    const accessState = `${agents}:${workflows}`
    if (lastAccessStateRef.current === accessState) {
      return
    }
    lastAccessStateRef.current = accessState
    const node = textareaRef.current
    if (!node || document.activeElement !== node) {
      return
    }
    syncSession(node.value, node.selectionStart ?? node.value.length)
  }, [agents, workflows, syncSession, textareaRef])

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
      const config =
        suggestion.kind === "agent" ? agentsConfig : workflowsConfig
      commitEdit(
        applyMentionInsertion(
          getText(),
          ranges,
          session,
          {
            kind: suggestion.kind,
            label: suggestion.label,
            targetId: suggestion.id,
          },
          config?.single ?? false
        )
      )
    },
    [agentsConfig, commitEdit, getText, ranges, session, workflowsConfig]
  )

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      // While an IME is composing, Enter, Tab and the arrows drive the
      // candidate window, so nothing here may claim them. `PromptInputTextarea`
      // guards its own submit the same way, but it runs this handler first and
      // then defers to whatever it prevented, so the check has to happen here.
      if (event.nativeEvent.isComposing) {
        return false
      }

      if (isOpen) {
        if (event.key === "Escape") {
          event.preventDefault()
          setSession(undefined)
          return true
        }

        // With nothing to pick, the navigation and selection keys belong to
        // the textarea: Enter inserts a newline or submits, depending on the
        // surface. A locked source is always empty, so its popover never
        // swallows a submit.
        if (items.length === 0) {
          return false
        }

        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault()
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
    workflowId: findWorkflowMention(ranges)?.targetId ?? null,
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
