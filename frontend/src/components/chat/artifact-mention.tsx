"use client"

import { LayersIcon, type LucideIcon } from "lucide-react"
import Link from "next/link"
import {
  type ComponentProps,
  createContext,
  type ReactNode,
  useContext,
} from "react"
import { type CaseReadMinimal, casesQueryCases } from "@/client"
import { SEVERITIES, STATUSES } from "@/components/cases/case-categories"
import { SmoothResponse } from "@/components/chat/smooth-response"
import {
  type ArtifactMention,
  isMentionPath,
  mentionFromHref,
  remarkArtifactMentions,
} from "@/lib/agent-artifact-mentions"
import { caseHref } from "@/lib/cases/urls"
import { useQuery } from "@/lib/query"
import { useOptionalWorkspaceId } from "@/providers/workspace-id"
import type { WorkspaceChatArtifact } from "@/types/workspace-chat-artifacts"

type OpenArtifact = (artifact: WorkspaceChatArtifact) => void

/**
 * Set on a chat surface that has an artifact panel. A case chip inside it opens
 * a panel tab; elsewhere the chip links to the case page.
 */
export const ArtifactPanelContext = createContext<OpenArtifact | null>(null)

/** A mentioned case as search returns it; search records no case view. */
function useMentionedCase(workspaceId: string, caseId: string) {
  return useQuery<CaseReadMinimal | null>({
    // Under ["cases", workspaceId] so every case edit refreshes it.
    queryKey: ["cases", workspaceId, "mention", caseId],
    queryFn: async () => {
      const page = await casesQueryCases({
        workspaceId,
        requestBody: { case_id: caseId, limit: 1, include_count: false },
      })
      return page.items[0] ?? null
    },
    retry: false,
    meta: { suppressErrorToast: true },
  })
}

/** A case badge as a span: the chip sits inside a paragraph, link, or button. */
function InlineCaseBadge({
  label,
  icon: Icon,
}: {
  label: string
  icon: LucideIcon
}) {
  return (
    <span className="inline-flex items-center gap-0.5 whitespace-nowrap text-[0.85em] font-normal text-muted-foreground">
      <Icon className="size-[0.9em] shrink-0" strokeWidth={3} />
      {label}
    </span>
  )
}

const CHIP_CLASS =
  "inline-flex max-w-full items-center gap-1 whitespace-nowrap rounded-md border bg-muted/40 px-1.5 align-baseline text-[0.9em] font-medium leading-snug no-underline hover:bg-muted"

/** A case chip with its live status and severity; label only until the viewer may see it. */
function CaseMentionChip({
  workspaceId,
  mention,
  children,
}: {
  workspaceId: string
  mention: ArtifactMention
  children: ReactNode
}) {
  const openArtifact = useContext(ArtifactPanelContext)
  const { data: mentioned, status } = useMentionedCase(workspaceId, mention.id)
  // Loading, unknown, or hidden; a failed refetch keeps stale data it must not show.
  if (status !== "success" || !mentioned) return <span>{children}</span>
  const body = (
    <>
      <LayersIcon className="size-3.5 shrink-0 text-muted-foreground" />
      {/* The case's own ID, so the chip never shows one case's label on another. */}
      <span className="shrink-0">{mentioned.short_id}</span>
      {/* One line: a badge that does not fit wraps out of sight; the ID never truncates. */}
      <span className="flex h-[1lh] min-w-0 flex-wrap items-center gap-x-1 overflow-hidden">
        <InlineCaseBadge {...STATUSES[mentioned.status]} />
        <InlineCaseBadge {...SEVERITIES[mentioned.severity]} />
      </span>
    </>
  )
  if (!openArtifact) {
    return (
      <Link href={caseHref(workspaceId, mention.id)} className={CHIP_CLASS}>
        {body}
      </Link>
    )
  }
  return (
    <button
      type="button"
      className={CHIP_CLASS}
      onClick={() =>
        openArtifact({
          type: "case",
          id: mention.id,
          title: mentioned.summary,
          severity: mentioned.severity,
          status: mentioned.status,
        })
      }
    >
      {body}
    </button>
  )
}

/** Mirrors Streamdown's default link, which it does not export. */
export function MarkdownLink({
  href,
  children,
  ...props
}: ComponentProps<"a">) {
  return (
    <a
      className="wrap-anywhere font-medium text-primary underline"
      data-incomplete={href === "streamdown:incomplete-link"}
      data-streamdown="link"
      href={href}
      rel="noreferrer"
      target="_blank"
      {...props}
    >
      {children}
    </a>
  )
}

/** The `a` component for agent replies: case mentions become chips; other links are unchanged. */
export function ArtifactMentionLink({
  href,
  children,
  node: _node,
  ...props
}: ComponentProps<"a"> & { node?: unknown }) {
  const workspaceId = useOptionalWorkspaceId()
  const mention = mentionFromHref(href)
  // A reserved path that is not a case mention is a dead link: show its text.
  if (!mention && isMentionPath(href)) return <span>{children}</span>
  if (!mention) {
    return (
      <MarkdownLink href={href} {...props}>
        {children}
      </MarkdownLink>
    )
  }
  if (!workspaceId) return <span>{children}</span>
  return (
    <CaseMentionChip workspaceId={workspaceId} mention={mention}>
      {children}
    </CaseMentionChip>
  )
}

const MENTION_REMARK_PLUGINS = [remarkArtifactMentions]
const MENTION_COMPONENTS = { a: ArtifactMentionLink }

/** An agent reply's text, with its case mentions drawn as chips. */
export function AgentReplyText({
  text,
  animate,
}: {
  text: string
  animate: boolean
}) {
  return (
    <SmoothResponse
      text={text}
      animate={animate}
      remarkPlugins={MENTION_REMARK_PLUGINS}
      components={MENTION_COMPONENTS}
    />
  )
}
