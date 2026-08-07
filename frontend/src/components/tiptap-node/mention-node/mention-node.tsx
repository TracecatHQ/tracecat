"use client"

import {
  mergeAttributes,
  Node,
  type NodeViewProps,
  NodeViewWrapper,
  ReactNodeViewRenderer,
} from "@tiptap/react"
import { useAgentPresets } from "@/hooks/use-agent-presets"
import { type MentionTarget, parseMentionHref } from "@/lib/mentions"
import { cn } from "@/lib/utils"
import { useOptionalWorkspaceId } from "@/providers/workspace-id"

/**
 * Resolution state of a mention chip.
 *
 * - `resolved`: the target was found and the chip shows its current name.
 * - `loading`: the target directory has not arrived yet; the chip shows the
 *   authored label in the normal treatment so nothing flashes.
 * - `unavailable`: the target does not exist (deleted, or a hand-typed id).
 */
export type MentionChipState = "resolved" | "loading" | "unavailable"

function mentionChipClassName(state: MentionChipState): string {
  switch (state) {
    case "unavailable":
      return "border-dashed border-border/70 bg-muted/30 text-muted-foreground"
    default:
      return "border-border bg-muted text-foreground"
  }
}

/**
 * Presentational, inert mention chip. Exported separately from the node so it
 * can be tested without an editor.
 */
export function MentionChip({
  label,
  state,
  title,
}: {
  /** Text rendered inside the chip, including its leading `@`. */
  label: string
  state: MentionChipState
  /** Optional native tooltip, used to explain the unavailable state. */
  title?: string
}) {
  return (
    <span
      className={cn(
        "inline-flex items-baseline rounded-md border px-1.5 align-baseline text-xs font-medium",
        mentionChipClassName(state)
      )}
      data-state={state}
      data-testid="mention-chip"
      title={title}
    >
      {label}
    </span>
  )
}

/**
 * Chip for `mention://agent/<preset id>`.
 *
 * The name always comes from the preset resolved by id, never from the label
 * embedded in the markdown: labels can be hand-typed to disagree with the id
 * and go stale when a preset is renamed.
 */
export function AgentMentionChip({
  presetId,
  label,
}: {
  /** Preset id taken from the mention href; the only trusted identifier. */
  presetId: string
  /** Label authored in the markdown, used only until the id resolves. */
  label: string
}) {
  const workspaceId = useOptionalWorkspaceId()
  const { presets, presetsIsLoading, presetsError } =
    useAgentPresets(workspaceId)

  const preset = presets?.find((candidate) => candidate.id === presetId)
  if (preset) {
    return <MentionChip label={`@${preset.name}`} state="resolved" />
  }

  if (!presetsError && (presetsIsLoading || presets === undefined)) {
    return <MentionChip label={label} state="loading" />
  }

  return (
    <MentionChip
      label={label}
      state="unavailable"
      title="Agent preset unavailable"
    />
  )
}

function MentionTargetChip({
  target,
  label,
}: {
  target: MentionTarget
  label: string
}) {
  switch (target.type) {
    case "agent":
      return <AgentMentionChip presetId={target.presetId} label={label} />
  }
}

function MentionNodeView({ node }: NodeViewProps) {
  const href = typeof node.attrs.href === "string" ? node.attrs.href : null
  const label = typeof node.attrs.label === "string" ? node.attrs.label : ""
  const target = parseMentionHref(href)

  // Unreachable in practice: the node is only ever created for hrefs that
  // already parsed. Kept so a malformed href can never render as a chip.
  if (!target) {
    return (
      <NodeViewWrapper as="span" className="mention">
        <a href={href ?? undefined}>{label}</a>
      </NodeViewWrapper>
    )
  }

  return (
    <NodeViewWrapper as="span" className="mention">
      <MentionTargetChip target={target} label={label} />
    </NodeViewWrapper>
  )
}

/**
 * Inline atom node that takes over `mention://` links so they render as chips
 * instead of anchors.
 *
 * Interception happens on the already-parsed href in both content pipelines:
 * the markdown `link` token (how case comments are loaded) and the `a[href]`
 * HTML parse rule (inline HTML and `setContent` with HTML). Hrefs that are not
 * well-formed mentions are handed back to the Link mark untouched.
 */
export const Mention = Node.create({
  name: "mention",
  group: "inline",
  inline: true,
  atom: true,
  selectable: false,
  // Extension priority also decides markdown handler registration order, and
  // this node has to be registered before the Link mark to claim `link` tokens.
  priority: 1100,

  addAttributes() {
    return {
      href: {
        default: null,
        parseHTML: (element) => element.getAttribute("href"),
      },
      label: {
        default: "",
        parseHTML: (element) => element.textContent ?? "",
        // Rendered as the anchor's text content, not as an attribute.
        renderHTML: () => ({}),
      },
    }
  },

  parseHTML() {
    return [
      {
        tag: "a[href]",
        // ProseMirror sorts node and mark parse rules into one list and tries
        // marks first at equal priority, so this rule needs to outrank the Link
        // mark's default of 50. Extension priority does not reach parse rules.
        priority: 1000,
        getAttrs: (element) => {
          const href = element.getAttribute("href")
          if (!parseMentionHref(href)) {
            // Not a mention: fall through to the Link mark.
            return false
          }
          return { href, label: element.textContent ?? "" }
        },
      },
    ]
  },

  renderHTML({ node, HTMLAttributes }) {
    const label = typeof node.attrs.label === "string" ? node.attrs.label : ""
    return ["a", mergeAttributes(HTMLAttributes), label]
  },

  renderText({ node }) {
    return typeof node.attrs.label === "string" ? node.attrs.label : ""
  },

  markdownTokenName: "link",

  parseMarkdown(token, helpers) {
    const href = typeof token.href === "string" ? token.href : null
    if (parseMentionHref(href)) {
      return helpers.createNode("mention", { href, label: token.text ?? "" })
    }
    // Ordinary link: reproduce the Link mark's own markdown handling, because
    // only the first registered handler for a token type is consulted. Keep
    // this in step with `@tiptap/extension-link`'s `parseMarkdown`.
    return helpers.applyMark("link", helpers.parseInline(token.tokens ?? []), {
      href: token.href,
      title: token.title || null,
    })
  },

  renderMarkdown(node) {
    const label = typeof node.attrs?.label === "string" ? node.attrs.label : ""
    const href = typeof node.attrs?.href === "string" ? node.attrs.href : ""
    return `[${label}](${href})`
  },

  addNodeView() {
    return ReactNodeViewRenderer(MentionNodeView)
  },
})
