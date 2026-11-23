"use client"

import { LayersIcon } from "lucide-react"
import Link from "next/link"
import {
  type ComponentProps,
  createContext,
  type ReactNode,
  useContext,
} from "react"
import { type CaseReadMinimal, casesQueryCases } from "@/client"
import { CaseBadge } from "@/components/cases/case-badge"
import { SEVERITIES, STATUSES } from "@/components/cases/case-categories"
import { SmoothResponse } from "@/components/chat/smooth-response"
import {
  type ArtifactMention,
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

const CHIP_CLASS =
  "inline-flex max-w-full items-center gap-1 rounded-md border bg-muted/40 px-1.5 align-baseline text-[0.9em] font-medium leading-snug no-underline hover:bg-muted"

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
  const { data: mentioned } = useMentionedCase(workspaceId, mention.id)
  // Loading, unknown, or hidden from the viewer.
  if (!mentioned) return <span>{children}</span>
  const body = (
    <>
      <LayersIcon className="size-3.5 shrink-0 text-muted-foreground" />
      <span className="truncate">{children}</span>
      <CaseBadge
        {...STATUSES[mentioned.status]}
        color={undefined}
        className="text-[0.85em]"
      />
      <CaseBadge
        {...SEVERITIES[mentioned.severity]}
        color={undefined}
        className="text-[0.85em]"
      />
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

/** The `a` component for agent replies: case mentions become chips; other links are unchanged. */
export function ArtifactMentionLink({
  href,
  children,
  node: _node,
  ...props
}: ComponentProps<"a"> & { node?: unknown }) {
  const workspaceId = useOptionalWorkspaceId()
  const mention = mentionFromHref(href)
  if (!mention) {
    // Mirrors Streamdown's default link, which it does not export.
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
