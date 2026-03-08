/**
 * @jest-environment jsdom
 */

import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import type { CaseCommentThreadRead } from "@/client"
import { CommentSection } from "@/components/cases/case-comments-section"
import { useAuth } from "@/hooks/use-auth"
import {
  useCaseCommentThreads,
  useCreateCaseComment,
  useDeleteCaseComment,
  useUpdateCaseComment,
} from "@/lib/hooks"

jest.mock("@/hooks/use-auth", () => ({
  useAuth: jest.fn(),
}))

jest.mock("@/lib/hooks", () => ({
  useCaseCommentThreads: jest.fn(),
  useCreateCaseComment: jest.fn(),
  useDeleteCaseComment: jest.fn(),
  useUpdateCaseComment: jest.fn(),
}))

jest.mock("@/components/cases/case-description-editor", () => ({
  CaseCommentViewer: ({ content }: { content: string }) => <div>{content}</div>,
}))

jest.mock("@/components/cases/case-panel-common", () => ({
  CaseEventTimestamp: ({
    createdAt,
  }: {
    createdAt: string
    lastEditedAt?: string | null
  }) => <span>{createdAt}</span>,
  CaseUserAvatar: () => <div>avatar</div>,
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

const mockUseAuth = useAuth as jest.MockedFunction<typeof useAuth>
const mockUseCaseCommentThreads = useCaseCommentThreads as jest.MockedFunction<
  typeof useCaseCommentThreads
>
const mockUseCreateCaseComment = useCreateCaseComment as jest.MockedFunction<
  typeof useCreateCaseComment
>
const mockUseDeleteCaseComment = useDeleteCaseComment as jest.MockedFunction<
  typeof useDeleteCaseComment
>
const mockUseUpdateCaseComment = useUpdateCaseComment as jest.MockedFunction<
  typeof useUpdateCaseComment
>

function createThreadFixtures(): CaseCommentThreadRead[] {
  return [
    {
      comment: {
        id: "comment-1",
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
        content: "Top level",
        parent_id: null,
        user: {
          id: "user-1",
          email: "owner@example.com",
          role: "admin",
          first_name: "Owner",
          last_name: "One",
          settings: {},
        },
        last_edited_at: null,
        deleted_at: null,
        is_deleted: false,
      },
      replies: [
        {
          id: "comment-2",
          created_at: "2024-01-01T01:00:00Z",
          updated_at: "2024-01-01T01:00:00Z",
          content: "Reply one",
          parent_id: "comment-1",
          user: {
            id: "user-2",
            email: "reply@example.com",
            role: "admin",
            first_name: "Reply",
            last_name: "User",
            settings: {},
          },
          last_edited_at: null,
          deleted_at: null,
          is_deleted: false,
        },
      ],
      reply_count: 1,
      last_activity_at: "2024-01-01T01:00:00Z",
    },
    {
      comment: {
        id: "comment-3",
        created_at: "2024-01-02T00:00:00Z",
        updated_at: "2024-01-02T00:00:00Z",
        content: "Comment deleted",
        parent_id: null,
        user: {
          id: "user-3",
          email: "deleted@example.com",
          role: "admin",
          first_name: "Deleted",
          last_name: "User",
          settings: {},
        },
        last_edited_at: null,
        deleted_at: "2024-01-02T00:00:00Z",
        is_deleted: true,
      },
      replies: [],
      reply_count: 0,
      last_activity_at: "2024-01-02T00:00:00Z",
    },
  ]
}

describe("CommentSection", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockUseAuth.mockReturnValue({
      user: { id: "user-1" },
      userIsLoading: false,
      userError: null,
    } as ReturnType<typeof useAuth>)
    mockUseCaseCommentThreads.mockReturnValue({
      caseCommentThreads: createThreadFixtures(),
      caseCommentThreadsIsLoading: false,
      caseCommentThreadsError: null,
    })
    mockUseCreateCaseComment.mockReturnValue({
      createComment: jest.fn().mockResolvedValue(undefined),
      createCommentIsPending: false,
      createCommentError: null,
    })
    mockUseDeleteCaseComment.mockReturnValue({
      deleteComment: jest.fn().mockResolvedValue(undefined),
      deleteCommentIsPending: false,
      deleteCommentError: null,
    })
    mockUseUpdateCaseComment.mockReturnValue({
      updateComment: jest.fn().mockResolvedValue(undefined),
      updateCommentIsPending: false,
      updateCommentError: null,
    })
  })

  it("renders grouped threads, replies, and a tombstone row", () => {
    render(<CommentSection caseId="case-1" workspaceId="workspace-1" />)

    expect(screen.getByText("Top level")).toBeInTheDocument()
    expect(screen.getByText("Reply one")).toBeInTheDocument()
    expect(screen.getByText("Comment deleted")).toBeInTheDocument()
  })

  it("shows inline reply composer only for active top-level comments and hides controls on tombstones", () => {
    render(<CommentSection caseId="case-1" workspaceId="workspace-1" />)

    expect(screen.getByPlaceholderText("Leave a reply...")).toBeInTheDocument()
    expect(screen.queryAllByPlaceholderText("Leave a reply...")).toHaveLength(1)
    expect(screen.getByPlaceholderText("Leave a reply...")).toHaveClass(
      "resize-none"
    )
    expect(screen.getByPlaceholderText("Leave a comment...")).toHaveClass(
      "resize-none"
    )
    expect(screen.queryByText("Reply")).not.toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "More options" })
    ).toBeInTheDocument()
    expect(
      screen.queryAllByRole("button", { name: "More options" })
    ).toHaveLength(1)
    expect(
      screen.getByRole("button", { name: "Hide replies" })
    ).toBeInTheDocument()
  })

  it("toggles replies visibility for a parent thread", () => {
    render(<CommentSection caseId="case-1" workspaceId="workspace-1" />)

    fireEvent.click(screen.getByRole("button", { name: "Hide replies" }))

    expect(screen.queryByText("Reply one")).not.toBeInTheDocument()
    expect(
      screen.queryByPlaceholderText("Leave a reply...")
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Show replies" })
    ).toBeInTheDocument()
  })

  it("submits parent_id through the inline reply composer", async () => {
    const createComment = jest.fn().mockResolvedValue(undefined)
    mockUseCreateCaseComment.mockReturnValue({
      createComment,
      createCommentIsPending: false,
      createCommentError: null,
    })

    render(<CommentSection caseId="case-1" workspaceId="workspace-1" />)

    const replyInput = screen.getByPlaceholderText("Leave a reply...")
    fireEvent.change(replyInput, {
      target: { value: "New reply" },
    })
    const replyForm = replyInput.closest("form")
    if (!replyForm) {
      throw new Error("Reply form should exist")
    }
    fireEvent.click(within(replyForm).getByRole("button", { name: "Send" }))

    await waitFor(() => {
      expect(createComment).toHaveBeenCalledWith({
        content: "New reply",
        parent_id: "comment-1",
      })
    })

    await waitFor(() => {
      expect(screen.getByPlaceholderText("Leave a reply...")).toHaveValue("")
    })
  })
})
