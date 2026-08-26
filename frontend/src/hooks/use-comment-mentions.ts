"use client"

import type { KeyboardEvent, RefObject } from "react"
import { useCallback, useMemo, useState } from "react"
import type { AgentPresetReadMinimal } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { useAgentPresets } from "@/hooks/use-agent-presets"
import { useCommentWorkflows } from "@/hooks/use-comment-workflows"
import { useEntitlements } from "@/hooks/use-entitlements"
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
} from "@/lib/comment-mentions"
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
export interface UseCommentMentionsOptions {
  workspaceId: string
  textareaRef: RefObject<HTMLTextAreaElement | null>
  /** Current display text; read at edit time so splices see the old value. */
  getText: () => string
  /** Writes display text back to the owning form. */
  setText: (next: string) => void
  /** Whether `/` workflow commands are offered; the case add-ons entitlement. */
  workflowsEnabled: boolean
}

/**
 * Everything the composer needs to render and drive mentions: popover state for
 * the suggestion list, the ranges the highlight overlay paints, event handlers
 * to forward from the textarea, and the submit-time serializer.
 */
export interface CommentMentions {
  /** Mention ranges into the display text, for the overlay and serializer. */
  ranges: MentionRange[]
  /** True when `@` opens the agent popover for this user. */
  agentsEnabled: boolean
  /** True when `/` opens the workflow popover for this user. */
  workflowsEnabled: boolean
  /** True when the popover should be rendered. */
  isOpen: boolean
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
 * Mentions in a plain comment `<Textarea>`: `@`-autocomplete for agent presets,
 * `/`-autocomplete for workflows to run, plus the display-value mapping that
 * turns highlighted display text into the wire value on submit.
 *
 * Agents are gated on the `agent:execute` and `agent:read` scopes plus the
 * `agent_addons` and `case_addons` entitlements. Workflows are gated on the
 * `workflow:execute` and `workflow:read` scopes plus `workflowsEnabled` (the
 * `case_addons` entitlement, passed by the composer). When gated, the trigger
 * character behaves as plain text.
 *
 * While the popover is open with no rows to pick, Enter, Tab, and the arrow
 * keys fall through to the textarea so a query with no match still lets the
 * user type a newline or submit with Cmd/Ctrl+Enter.
 */
export function useCommentMentions({
  workspaceId,
  textareaRef,
  getText,
  setText,
  workflowsEnabled: workflowsEntitled,
}: UseCommentMentionsOptions): CommentMentions {
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
  const { hasEntitlement } = useEntitlements()
  const agentsEnabled =
    canUseAgents === true &&
    hasEntitlement("agent_addons") &&
    hasEntitlement("case_addons")
  const workflowsEnabled = workflowsEntitled && canExecuteWorkflows === true

  const { presets, presetsIsLoading } = useAgentPresets(workspaceId, {
    enabled: agentsEnabled,
  })
  const { items: workflows, isLoading: workflowsIsLoading } =
    useCommentWorkflows(workspaceId, workflowsEnabled)
  const [ranges, setRanges] = useState<MentionRange[]>([])
  const [session, setSession] = useState<ActiveSession | undefined>(undefined)

  const sections = useMemo<MentionSection[]>(() => {
    if (!session) {
      return []
    }
    const query = session.query.toLowerCase()
    if (session.kind === "workflow") {
      const items = workflows
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
          label: preset.name,
        })
      )
    if (items.length === 0) {
      return []
    }
    return [{ section: "agents", label: "Agents", items }]
  }, [session, presets, workflows])

  const items = useMemo(
    () => sections.flatMap((section) => section.items),
    [sections]
  )

  // Clamp on read so a shrinking suggestion list cannot strand the highlight.
  const activeIndex = session
    ? Math.min(session.activeIndex, Math.max(items.length - 1, 0))
    : 0
  const isOpen = session !== undefined

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
      const enabled = token.kind === "agent" ? agentsEnabled : workflowsEnabled
      if (!enabled) {
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
    [agentsEnabled, workflowsEnabled, sessionCaret, sessionStart, textareaRef]
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
          kind: suggestion.kind,
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

        // With nothing to pick, the navigation and selection keys belong to
        // the textarea: Enter inserts a newline and Cmd/Ctrl+Enter submits.
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

  let isLoading = false
  if (session?.kind === "agent") {
    isLoading = presetsIsLoading
  } else if (session?.kind === "workflow") {
    isLoading = workflowsIsLoading
  }

  return {
    ranges,
    agentsEnabled,
    workflowsEnabled,
    isOpen,
    kind: session?.kind,
    sections,
    itemCount: items.length,
    activeIndex,
    caret: session?.caret,
    isLoading,
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
