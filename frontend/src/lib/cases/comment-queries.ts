import type { QueryClient } from "@tanstack/react-query"

import type { CaseCommentRead, CaseCommentThreadRead } from "@/client"

/** Poll interval used while a case-comment agent invocation is active. */
export const CASE_COMMENT_ACTIVE_POLL_INTERVAL_MS = 1_000

/** Stable query keys for flat and threaded case-comment responses. */
export const caseCommentQueryKeys = {
  comments: (caseId: string, workspaceId: string) =>
    ["case-comments", caseId, workspaceId] as const,
  threads: (caseId: string, workspaceId: string) =>
    ["case-comment-threads", caseId, workspaceId] as const,
}

function commentHasActiveInvocation(comment: CaseCommentRead): boolean {
  return Boolean(
    comment.mentions?.some(({ invocation }) =>
      invocation
        ? invocation.status === "pending" || invocation.status === "running"
        : false
    )
  )
}

/** Return whether flat comment data contains a pending or running invocation. */
export function hasActiveCaseCommentInvocations(
  comments: CaseCommentRead[] | undefined
): boolean {
  return Boolean(comments?.some(commentHasActiveInvocation))
}

/** Return whether threaded comment data contains a pending or running invocation. */
export function hasActiveCaseCommentThreadInvocations(
  threads: CaseCommentThreadRead[] | undefined
): boolean {
  return Boolean(
    threads?.some(
      ({ comment, replies }) =>
        commentHasActiveInvocation(comment) ||
        Boolean(replies?.some(commentHasActiveInvocation))
    )
  )
}

/** Invalidate both flat and threaded case-comment query families. */
export function invalidateCaseCommentQueries(
  queryClient: QueryClient,
  caseId: string,
  workspaceId: string
): void {
  queryClient.invalidateQueries({
    queryKey: caseCommentQueryKeys.comments(caseId, workspaceId),
  })
  queryClient.invalidateQueries({
    queryKey: caseCommentQueryKeys.threads(caseId, workspaceId),
  })
}
