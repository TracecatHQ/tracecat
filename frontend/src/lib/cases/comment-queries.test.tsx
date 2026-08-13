import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"

import {
  type CaseCommentAgentInvocationStatus,
  type CaseCommentRead,
  type CaseCommentThreadRead,
  casesCreateComment,
  casesListComments,
  casesListCommentThreads,
} from "@/client"
import {
  CASE_COMMENT_ACTIVE_POLL_INTERVAL_MS,
  caseCommentQueryKeys,
  invalidateCaseCommentQueries,
} from "@/lib/cases/comment-queries"
import { ENTITY_TO_INVALIDATION } from "@/lib/chat"
import {
  useCaseComments,
  useCaseCommentThreads,
  useCreateCaseComment,
} from "@/lib/hooks"

jest.mock("@/client", () => ({
  casesCreateComment: jest.fn(),
  casesListComments: jest.fn(),
  casesListCommentThreads: jest.fn(),
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

jest.mock("@/lib/api", () => ({
  client: {},
  getBaseUrl: jest.fn(() => "http://localhost"),
}))

const caseId = "case-test"
const workspaceId = "workspace-test"
const timestamp = "2026-08-11T12:00:00Z"

function makeComment(
  id: string,
  status?: CaseCommentAgentInvocationStatus
): CaseCommentRead {
  return {
    id,
    created_at: timestamp,
    updated_at: timestamp,
    content: `${id} content`,
    mentions: status
      ? [
          {
            id: `${id}-mention`,
            target_type: "agent",
            target_id: `${id}-preset`,
            label: `${id} agent`,
            created_at: timestamp,
            invocation: {
              id: `${id}-invocation`,
              preset_name: `${id} agent`,
              preset_slug: `${id}-agent`,
              status,
            },
          },
        ]
      : [],
  }
}

function makeThread(
  root: CaseCommentRead,
  replies: CaseCommentRead[] = []
): CaseCommentThreadRead {
  return {
    comment: root,
    replies,
    reply_count: replies.length,
    last_activity_at: timestamp,
  }
}

function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        gcTime: Number.POSITIVE_INFINITY,
        retry: false,
      },
    },
  })
}

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }
}

async function flushImmediateTimers(): Promise<void> {
  await act(async () => {
    await jest.advanceTimersByTimeAsync(0)
  })
}

describe("case comment query behavior", () => {
  beforeEach(() => {
    jest.useFakeTimers()
    jest.clearAllMocks()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it("polls both public comment views only while an invocation is active", async () => {
    const pendingSource = makeComment("source", "pending")
    const runningReply = makeComment("running-reply", "running")
    const succeededSource = makeComment("source", "succeeded")
    const failedComment = makeComment("failed-source", "failed")
    const attributedReply: CaseCommentRead = {
      ...makeComment("agent-reply"),
      agent: {
        invocation_id: "source-invocation",
        preset_name: "source agent",
        preset_slug: "source-agent",
      },
    }

    jest
      .mocked(casesListComments)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([pendingSource])
      .mockResolvedValueOnce([succeededSource, failedComment, attributedReply])
    jest
      .mocked(casesListCommentThreads)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([
        makeThread(makeComment("thread-root"), [runningReply]),
      ])
      .mockResolvedValueOnce([makeThread(succeededSource, [attributedReply])])

    const queryClient = createQueryClient()
    const { result, unmount } = renderHook(
      () => ({
        flat: useCaseComments({ caseId, workspaceId }),
        threaded: useCaseCommentThreads({ caseId, workspaceId }),
      }),
      { wrapper: createWrapper(queryClient) }
    )
    await flushImmediateTimers()

    expect(result.current.flat.caseComments).toEqual([])
    expect(result.current.threaded.caseCommentThreads).toEqual([])
    expect(casesListComments).toHaveBeenCalledTimes(1)
    expect(casesListCommentThreads).toHaveBeenCalledTimes(1)

    await act(async () => {
      await jest.advanceTimersByTimeAsync(
        CASE_COMMENT_ACTIVE_POLL_INTERVAL_MS * 2
      )
    })
    expect(casesListComments).toHaveBeenCalledTimes(1)
    expect(casesListCommentThreads).toHaveBeenCalledTimes(1)

    act(() => {
      invalidateCaseCommentQueries(queryClient, caseId, workspaceId)
    })
    await flushImmediateTimers()
    expect(result.current.flat.caseComments).toEqual([pendingSource])
    expect(result.current.threaded.caseCommentThreads).toEqual([
      makeThread(makeComment("thread-root"), [runningReply]),
    ])

    await act(async () => {
      await jest.advanceTimersByTimeAsync(CASE_COMMENT_ACTIVE_POLL_INTERVAL_MS)
    })
    await flushImmediateTimers()
    expect(casesListComments).toHaveBeenCalledTimes(3)
    expect(casesListCommentThreads).toHaveBeenCalledTimes(3)
    await waitFor(() =>
      expect(result.current.flat.caseComments).toEqual([
        succeededSource,
        failedComment,
        attributedReply,
      ])
    )
    await waitFor(() =>
      expect(result.current.threaded.caseCommentThreads).toEqual([
        makeThread(succeededSource, [attributedReply]),
      ])
    )

    await act(async () => {
      await jest.advanceTimersByTimeAsync(
        CASE_COMMENT_ACTIVE_POLL_INTERVAL_MS * 3
      )
    })
    expect(casesListComments).toHaveBeenCalledTimes(3)
    expect(casesListCommentThreads).toHaveBeenCalledTimes(3)

    unmount()
  })

  it("invalidates flat and threaded caches from comment and agent-tool entry points", async () => {
    const queryClient = createQueryClient()
    const commentsKey = caseCommentQueryKeys.comments(caseId, workspaceId)
    const threadsKey = caseCommentQueryKeys.threads(caseId, workspaceId)
    const createdComment = makeComment("created")
    jest.mocked(casesCreateComment).mockResolvedValue(createdComment)

    queryClient.setQueryData(commentsKey, [makeComment("existing")])
    queryClient.setQueryData(threadsKey, [
      makeThread(makeComment("existing-thread")),
    ])

    const { result } = renderHook(
      () => useCreateCaseComment({ caseId, workspaceId }),
      { wrapper: createWrapper(queryClient) }
    )
    await act(async () => {
      await result.current.createComment({ content: "New comment" })
    })

    expect(queryClient.getQueryState(commentsKey)?.isInvalidated).toBe(true)
    expect(queryClient.getQueryState(threadsKey)?.isInvalidated).toBe(true)

    queryClient.setQueryData(commentsKey, [createdComment])
    queryClient.setQueryData(threadsKey, [makeThread(createdComment)])
    expect(queryClient.getQueryState(commentsKey)?.isInvalidated).toBe(false)
    expect(queryClient.getQueryState(threadsKey)?.isInvalidated).toBe(false)

    const caseInvalidation = ENTITY_TO_INVALIDATION.case
    expect(caseInvalidation.predicate("core.cases.create_comment")).toBe(true)
    act(() => {
      caseInvalidation.handler(queryClient, workspaceId, caseId)
    })

    expect(queryClient.getQueryState(commentsKey)?.isInvalidated).toBe(true)
    expect(queryClient.getQueryState(threadsKey)?.isInvalidated).toBe(true)
  })
})
