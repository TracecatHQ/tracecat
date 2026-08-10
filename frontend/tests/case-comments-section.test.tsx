/**
 * @jest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import {
  type AgentPresetReadMinimal,
  type CaseCommentThreadRead,
  foldersListFolders,
  workflowsListWorkflows,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CommentSection } from "@/components/cases/case-comments-section"
import { useAgentPresets } from "@/hooks/use-agent-presets"
import { useAuth } from "@/hooks/use-auth"
import { useEntitlements } from "@/hooks/use-entitlements"
import {
  useCaseComments,
  useCaseCommentThreads,
  useCompactWorkflowExecution,
  useCreateCaseComment,
  useDeleteCaseComment,
  useUpdateCaseComment,
} from "@/lib/hooks"
import { getTextareaCaretCoordinates } from "@/lib/textarea-caret"

jest.mock("@/client", () => {
  const actual = jest.requireActual("@/client")
  return {
    ...actual,
    foldersListFolders: jest.fn(),
    workflowsListWorkflows: jest.fn(),
  }
})

jest.mock("@/hooks/use-auth", () => ({
  useAuth: jest.fn(),
}))

jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: jest.fn(),
}))

jest.mock("@/hooks/use-agent-presets", () => ({
  useAgentPresets: jest.fn(),
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: jest.fn(),
}))

jest.mock("@/lib/textarea-caret", () => {
  const actual = jest.requireActual("@/lib/textarea-caret")
  return {
    ...actual,
    getTextareaCaretCoordinates: jest.fn(),
  }
})

jest.mock("@/lib/hooks", () => ({
  useCaseComments: jest.fn(),
  useCaseCommentThreads: jest.fn(),
  useCompactWorkflowExecution: jest.fn(),
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
const mockUseEntitlements = useEntitlements as jest.MockedFunction<
  typeof useEntitlements
>
const mockUseAgentPresets = useAgentPresets as jest.MockedFunction<
  typeof useAgentPresets
>
const mockUseScopeCheck = useScopeCheck as jest.MockedFunction<
  typeof useScopeCheck
>
const mockGetCaretCoordinates =
  getTextareaCaretCoordinates as jest.MockedFunction<
    typeof getTextareaCaretCoordinates
  >
const mockUseCaseComments = useCaseComments as jest.MockedFunction<
  typeof useCaseComments
>
const mockUseCaseCommentThreads = useCaseCommentThreads as jest.MockedFunction<
  typeof useCaseCommentThreads
>
const mockUseCompactWorkflowExecution =
  useCompactWorkflowExecution as jest.MockedFunction<
    typeof useCompactWorkflowExecution
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
const mockFoldersListFolders = foldersListFolders as jest.MockedFunction<
  typeof foldersListFolders
>
const mockWorkflowsListWorkflows =
  workflowsListWorkflows as jest.MockedFunction<typeof workflowsListWorkflows>

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}

global.ResizeObserver = ResizeObserverMock as typeof ResizeObserver

function renderCommentSection() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <CommentSection caseId="case-1" workspaceId="workspace-1" />
    </QueryClientProvider>
  )
}

function createThreadFixtures(): CaseCommentThreadRead[] {
  return [
    {
      comment: {
        id: "comment-1",
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
        content: "Top level",
        parent_id: null,
        workflow: null,
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
          workflow: null,
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
        workflow: null,
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

function createAgentPresetFixtures(): AgentPresetReadMinimal[] {
  return [
    {
      id: "preset-1",
      workspace_id: "workspace-1",
      name: "Triage agent",
      slug: "triage-agent",
      description: "Triages new cases",
      model_provider: "openai",
      model_name: "gpt-4o",
      created_at: "2024-01-01T00:00:00Z",
      updated_at: "2024-01-01T00:00:00Z",
    },
    {
      id: "preset-2",
      workspace_id: "workspace-1",
      name: "Malware agent",
      slug: "malware-agent",
      description: null,
      model_provider: "openai",
      model_name: "gpt-4o",
      created_at: "2024-01-01T00:00:00Z",
      updated_at: "2024-01-01T00:00:00Z",
    },
  ]
}

function mockAgentPresets(presets: AgentPresetReadMinimal[]) {
  mockUseAgentPresets.mockReturnValue({
    presets,
    presetsIsLoading: false,
    presetsError: null,
    refetchPresets: jest.fn(),
  } as unknown as ReturnType<typeof useAgentPresets>)
}

/** Mirror `useScopeCheck` semantics against a fixed set of granted scopes. */
function grantScopes(granted: string[]) {
  const scopes = new Set(granted)
  mockUseScopeCheck.mockImplementation((scope, scopeList, options) => {
    const required = [...(scope ? [scope] : []), ...(scopeList ?? [])]
    if (required.length === 0) {
      return true
    }
    return options?.all
      ? required.every((value) => scopes.has(value))
      : required.some((value) => scopes.has(value))
  })
}

function mockEntitlements(keys: string[]) {
  mockUseEntitlements.mockReturnValue({
    hasEntitlement: jest.fn().mockImplementation((key) => keys.includes(key)),
    isLoading: false,
    hasEntitlementData: true,
  })
}

describe("CommentSection", () => {
  beforeEach(() => {
    const fixtures = createThreadFixtures()
    const firstThread = fixtures[0]!
    const secondThread = fixtures[1]!
    const firstReply = firstThread.replies?.[0]

    if (!firstReply) {
      throw new Error("Expected first thread fixture to include a reply")
    }

    jest.clearAllMocks()
    mockUseAuth.mockReturnValue({
      user: { id: "user-1" },
      userIsLoading: false,
      userError: null,
    } as ReturnType<typeof useAuth>)
    mockEntitlements(["case_addons"])
    mockUseScopeCheck.mockReturnValue(true)
    // Derive coordinates from the measured offset so a re-measure is visible.
    mockGetCaretCoordinates.mockImplementation((_textarea, position) => ({
      top: 100 + position,
      left: 10 + position,
      height: 16,
    }))
    mockAgentPresets(createAgentPresetFixtures())
    mockUseCaseComments.mockReturnValue({
      caseComments: [firstThread.comment, firstReply, secondThread.comment],
      caseCommentsIsLoading: false,
      caseCommentsError: null,
    })
    mockUseCaseCommentThreads.mockReturnValue({
      caseCommentThreads: fixtures,
      caseCommentThreadsIsLoading: false,
      caseCommentThreadsError: null,
    })
    mockUseCompactWorkflowExecution.mockReturnValue({
      execution: null,
      executionIsLoading: false,
      executionError: null,
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
    mockFoldersListFolders.mockResolvedValue([
      {
        id: "folder-1",
        name: "Incidents",
        path: "/Security/Incidents",
        workspace_id: "workspace-1",
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
      },
    ])
    mockWorkflowsListWorkflows.mockResolvedValue({
      items: [
        {
          id: "workflow-1",
          title: "Escalate case",
          description: "",
          status: "online",
          icon_url: null,
          created_at: "2024-01-01T00:00:00Z",
          updated_at: "2024-01-01T00:00:00Z",
          version: 1,
          alias: "escalate_case",
          error_handler: null,
          latest_definition: null,
          folder_id: "folder-1",
          trigger_summary: null,
          tags: [
            {
              id: "tag-1",
              name: "priority",
              color: "#111111",
              ref: "priority",
            },
          ],
        },
      ],
      next_cursor: null,
    })
  })

  it("renders grouped threads, replies, and a tombstone row", () => {
    renderCommentSection()

    expect(screen.getByText("Top level")).toBeInTheDocument()
    expect(screen.getByText("Reply one")).toBeInTheDocument()
    expect(screen.getByText("Comment deleted")).toBeInTheDocument()
  })

  it("shows inline reply composer only for active top-level comments and hides controls on tombstones", () => {
    renderCommentSection()

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
    renderCommentSection()

    fireEvent.click(screen.getByRole("button", { name: "Hide replies" }))

    expect(screen.queryByText("Reply one")).not.toBeInTheDocument()
    expect(
      screen.queryByPlaceholderText("Leave a reply...")
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Show replies" })
    ).toBeInTheDocument()
  })

  it("shows the replies toggle for non-owners", () => {
    mockUseAuth.mockReturnValue({
      user: { id: "someone-else" },
      userIsLoading: false,
      userError: null,
    } as ReturnType<typeof useAuth>)

    renderCommentSection()

    expect(
      screen.getByRole("button", { name: "Hide replies" })
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "More options" })
    ).not.toBeInTheDocument()
  })

  it("keeps deleted parent threads collapsible when replies exist", () => {
    mockUseCaseCommentThreads.mockReturnValue({
      caseCommentThreads: [
        {
          comment: {
            id: "deleted-thread",
            created_at: "2024-01-02T00:00:00Z",
            updated_at: "2024-01-02T00:00:00Z",
            content: "Comment deleted",
            parent_id: null,
            workflow: null,
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
          replies: [
            {
              id: "deleted-thread-reply",
              created_at: "2024-01-02T01:00:00Z",
              updated_at: "2024-01-02T01:00:00Z",
              content: "Reply on deleted thread",
              parent_id: "deleted-thread",
              workflow: null,
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
          last_activity_at: "2024-01-02T01:00:00Z",
        },
      ],
      caseCommentThreadsIsLoading: false,
      caseCommentThreadsError: null,
    })

    renderCommentSection()

    expect(
      screen.getByRole("button", { name: "Hide replies" })
    ).toBeInTheDocument()
    expect(
      screen.queryByPlaceholderText("Leave a reply...")
    ).not.toBeInTheDocument()
    expect(screen.getByText("Reply on deleted thread")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Hide replies" }))

    expect(
      screen.queryByText("Reply on deleted thread")
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

    renderCommentSection()

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

  it("renders flat top-level comments only without case add-ons", () => {
    mockUseEntitlements.mockReturnValue({
      hasEntitlement: jest.fn().mockReturnValue(false),
      isLoading: false,
      hasEntitlementData: true,
    })

    renderCommentSection()

    expect(screen.getByText("Top level")).toBeInTheDocument()
    expect(screen.queryByText("Reply one")).not.toBeInTheDocument()
    expect(
      screen.queryByPlaceholderText("Leave a reply...")
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Hide replies" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /no workflow selected/i })
    ).not.toBeInTheDocument()
    expect(
      screen.getByPlaceholderText("Leave a comment...")
    ).toBeInTheDocument()
  })

  it("shows workflow selectors when case add-ons are enabled", async () => {
    renderCommentSection()

    await waitFor(() => {
      expect(
        screen.getAllByRole("button", { name: /no workflow selected/i })
      ).toHaveLength(2)
    })
  })

  it("submits trimmed workflow-backed parent comments and clears the selector", async () => {
    const createComment = jest.fn().mockResolvedValue(undefined)
    mockUseCreateCaseComment.mockReturnValue({
      createComment,
      createCommentIsPending: false,
      createCommentError: null,
    })

    renderCommentSection()

    await waitFor(() => {
      expect(
        screen.getAllByRole("button", { name: /no workflow selected/i })[0]
      ).toBeInTheDocument()
    })

    const commentInput = screen
      .getAllByPlaceholderText("Leave a comment...")
      .at(-1)
    if (!commentInput) {
      throw new Error("Root comment input should exist")
    }
    const parentForm = commentInput.closest("form")
    if (!parentForm) {
      throw new Error("Comment form should exist")
    }

    fireEvent.click(
      within(parentForm).getByRole("button", { name: /no workflow selected/i })
    )
    fireEvent.click(screen.getByRole("option", { name: /Escalate case/i }))

    await waitFor(() => {
      expect(
        within(parentForm).getByRole("button", { name: /Escalate case/i })
      ).toBeInTheDocument()
    })

    fireEvent.change(commentInput, {
      target: { value: "  Run this workflow  " },
    })
    fireEvent.click(within(parentForm).getByRole("button", { name: "Send" }))

    await waitFor(() => {
      expect(createComment).toHaveBeenCalledWith(
        expect.objectContaining({
          content: "Run this workflow",
          workflow_id: "workflow-1",
        })
      )
    })

    await waitFor(() => {
      expect(
        within(parentForm).getByRole("button", {
          name: /no workflow selected/i,
        })
      ).toBeInTheDocument()
    })
  })

  it("renders workflow-backed comments with workflow metadata and run link", () => {
    mockUseCaseCommentThreads.mockReturnValue({
      caseCommentThreads: [
        {
          comment: {
            id: "workflow-comment-1",
            created_at: "2024-01-01T00:00:00Z",
            updated_at: "2024-01-01T00:00:00Z",
            content: "Kick off the workflow",
            parent_id: null,
            workflow: {
              workflow_id: "workflow-1",
              title: "Escalate case",
              alias: "escalate_case",
              wf_exec_id: "wf_123/exec_456",
              status: "running",
            },
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
          replies: [],
          reply_count: 0,
          last_activity_at: "2024-01-01T00:00:00Z",
        },
      ],
      caseCommentThreadsIsLoading: false,
      caseCommentThreadsError: null,
    })
    mockUseCaseComments.mockReturnValue({
      caseComments: [],
      caseCommentsIsLoading: false,
      caseCommentsError: null,
    })
    mockUseCompactWorkflowExecution.mockReturnValue({
      execution: {
        id: "wf_123/exec_456",
        status: "COMPLETED",
      } as ReturnType<typeof useCompactWorkflowExecution>["execution"],
      executionIsLoading: false,
      executionError: null,
    })

    renderCommentSection()

    expect(screen.getByText("Escalate case")).toBeInTheDocument()
    expect(screen.getByText("escalate_case")).toBeInTheDocument()
    expect(
      screen.getByRole("link", { name: "Open workflow run" })
    ).toHaveAttribute(
      "href",
      "/workspaces/workspace-1/workflows/wf_123/executions/exec_456"
    )
    expect(screen.queryByText("avatar")).not.toBeInTheDocument()
  })

  it("falls back to the comment execution id for the workflow run link", () => {
    mockUseCaseCommentThreads.mockReturnValue({
      caseCommentThreads: [
        {
          comment: {
            id: "workflow-comment-2",
            created_at: "2024-01-01T00:00:00Z",
            updated_at: "2024-01-01T00:00:00Z",
            content: "Kick off the workflow",
            parent_id: null,
            workflow: {
              workflow_id: "workflow-1",
              title: "Escalate case",
              alias: "escalate_case",
              wf_exec_id: "wf_789/exec_999",
              status: "running",
            },
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
          replies: [],
          reply_count: 0,
          last_activity_at: "2024-01-01T00:00:00Z",
        },
      ],
      caseCommentThreadsIsLoading: false,
      caseCommentThreadsError: null,
    })
    mockUseCaseComments.mockReturnValue({
      caseComments: [],
      caseCommentsIsLoading: false,
      caseCommentsError: null,
    })
    mockUseCompactWorkflowExecution.mockReturnValue({
      execution: null,
      executionIsLoading: false,
      executionError: null,
    })

    renderCommentSection()

    expect(
      screen.getByRole("link", { name: "Open workflow run" })
    ).toHaveAttribute(
      "href",
      "/workspaces/workspace-1/workflows/wf_789/executions/exec_999"
    )
  })

  describe("agent mention autocomplete", () => {
    beforeEach(() => {
      mockEntitlements(["case_addons", "agent_addons"])
    })

    function typeInto(
      textarea: HTMLElement,
      value: string,
      caret = value.length
    ) {
      fireEvent.change(textarea, {
        target: { value, selectionStart: caret },
      })
    }

    function getComposer() {
      return screen.getByPlaceholderText("Leave a comment...")
    }

    function getCaretMarker(textarea: HTMLElement) {
      const marker = textarea.parentElement?.querySelector("span[aria-hidden]")
      if (!(marker instanceof HTMLElement)) {
        throw new Error("Expected a caret marker next to the composer")
      }
      return marker
    }

    it("opens the popover listing presets under a section header", () => {
      renderCommentSection()

      typeInto(getComposer(), "@")

      expect(screen.getByText("Agents")).toBeInTheDocument()
      expect(screen.getByText("Triage agent")).toBeInTheDocument()
      expect(screen.getByText("Malware agent")).toBeInTheDocument()
    })

    it("renders the popover above the caret and outside the composer form", () => {
      renderCommentSection()

      typeInto(getComposer(), "@")

      const content = screen.getByText("Triage agent").closest("[data-side]")
      expect(content).toHaveAttribute("data-side", "top")
      // Portaled, so the thread's overflow-hidden cannot clip the popover.
      expect(getComposer().closest("form")).not.toContainElement(
        content as HTMLElement | null
      )
      // Anchored to a caret marker rather than the composer itself.
      expect(getCaretMarker(getComposer())).toBeInTheDocument()
    })

    it("pins the anchor to the @ offset and holds it as the query grows", () => {
      renderCommentSection()
      const composer = getComposer()

      typeInto(composer, "Ping @")

      // Measured once, at the offset of the `@` rather than the caret.
      expect(mockGetCaretCoordinates).toHaveBeenCalledTimes(1)
      expect(mockGetCaretCoordinates).toHaveBeenLastCalledWith(composer, 5)
      const marker = getCaretMarker(composer)
      expect(marker.style.left).toBe("15px")

      typeInto(composer, "Ping @tri")

      expect(mockGetCaretCoordinates).toHaveBeenCalledTimes(1)
      expect(getCaretMarker(composer).style.left).toBe("15px")
    })

    it("re-measures when a new mention session starts", () => {
      renderCommentSection()
      const composer = getComposer()

      typeInto(composer, "@a")
      expect(mockGetCaretCoordinates).toHaveBeenLastCalledWith(composer, 0)

      typeInto(composer, "@a done @")

      expect(mockGetCaretCoordinates).toHaveBeenCalledTimes(2)
      expect(mockGetCaretCoordinates).toHaveBeenLastCalledWith(composer, 8)
      expect(getCaretMarker(composer).style.left).toBe("18px")
    })

    it("keeps the popover open with an empty state when nothing matches", () => {
      renderCommentSection()
      const composer = getComposer()

      typeInto(composer, "@zzz")

      expect(screen.getByText("No agents found")).toBeInTheDocument()
      expect(screen.queryByText("Agents")).not.toBeInTheDocument()

      fireEvent.keyDown(composer, { key: "Escape" })
      expect(screen.queryByText("No agents found")).not.toBeInTheDocument()
    })

    it("filters presets by the mention query", () => {
      renderCommentSection()

      typeInto(getComposer(), "Ping @mal")

      expect(screen.getByText("Malware agent")).toBeInTheDocument()
      expect(screen.queryByText("Triage agent")).not.toBeInTheDocument()
    })

    it("ignores @ that does not follow whitespace", () => {
      renderCommentSection()

      typeInto(getComposer(), "email@tri")

      expect(screen.queryByText("Triage agent")).not.toBeInTheDocument()
    })

    it("dismisses on whitespace in the query and on Escape", () => {
      renderCommentSection()
      const composer = getComposer()

      typeInto(composer, "@tri ")
      expect(screen.queryByText("Triage agent")).not.toBeInTheDocument()

      typeInto(composer, "@tri")
      expect(screen.getByText("Triage agent")).toBeInTheDocument()

      fireEvent.keyDown(composer, { key: "Escape" })
      expect(screen.queryByText("Triage agent")).not.toBeInTheDocument()
    })

    it("selects with the keyboard without submitting the comment", async () => {
      const createComment = jest.fn().mockResolvedValue(undefined)
      mockUseCreateCaseComment.mockReturnValue({
        createComment,
        createCommentIsPending: false,
        createCommentError: null,
      })

      renderCommentSection()
      const composer = getComposer()

      typeInto(composer, "Ping @")
      fireEvent.keyDown(composer, { key: "ArrowDown" })
      fireEvent.keyDown(composer, { key: "Enter" })

      // The textarea holds display text, not the wire token.
      await waitFor(() => expect(composer).toHaveValue("Ping @Malware agent "))
      expect(createComment).not.toHaveBeenCalled()
      expect(
        screen.queryByRole("button", { name: "Malware agent" })
      ).not.toBeInTheDocument()
      await waitFor(() =>
        expect((composer as HTMLTextAreaElement).selectionStart).toBe(
          "Ping @Malware agent ".length
        )
      )
    })

    it("selects with tab without moving focus or submitting", async () => {
      const createComment = jest.fn().mockResolvedValue(undefined)
      mockUseCreateCaseComment.mockReturnValue({
        createComment,
        createCommentIsPending: false,
        createCommentError: null,
      })

      renderCommentSection()
      const composer = getComposer()
      composer.focus()

      typeInto(composer, "Ping @")
      // Shift+Tab falls through to native focus handling, so nothing is picked.
      fireEvent.keyDown(composer, { key: "Tab", shiftKey: true })
      expect(composer).toHaveValue("Ping @")

      fireEvent.keyDown(composer, { key: "Tab" })

      await waitFor(() => expect(composer).toHaveValue("Ping @Triage agent "))
      expect(createComment).not.toHaveBeenCalled()
      expect(composer).toHaveFocus()
    })

    it("wraps keyboard navigation with ArrowUp", async () => {
      renderCommentSection()
      const composer = getComposer()

      typeInto(composer, "@")
      fireEvent.keyDown(composer, { key: "ArrowUp" })
      fireEvent.keyDown(composer, { key: "Enter" })

      await waitFor(() => expect(composer).toHaveValue("@Malware agent "))
    })

    it("selects with the mouse from the inline reply composer", async () => {
      renderCommentSection()
      const reply = screen.getByPlaceholderText("Leave a reply...")

      typeInto(reply, "@tri")
      fireEvent.click(screen.getByRole("button", { name: "Triage agent" }))

      await waitFor(() => expect(reply).toHaveValue("@Triage agent "))
      expect(getComposer()).toHaveValue("")
    })

    it("highlights the inserted mention in the overlay", async () => {
      renderCommentSection()
      const composer = getComposer()

      typeInto(composer, "@tri")
      fireEvent.click(screen.getByRole("button", { name: "Triage agent" }))

      await waitFor(() => expect(composer).toHaveValue("@Triage agent "))
      const overlay = composer.parentElement?.querySelector(
        "[data-testid='comment-mention-overlay']"
      )
      const highlighted = overlay?.querySelector("[data-mention-target]")
      expect(highlighted).toHaveTextContent("@Triage agent")
      expect(highlighted).toHaveAttribute("data-mention-target", "preset-1")
      expect(highlighted).toHaveClass("text-primary")
      // No bold: it would change glyph widths and desync the caret.
      expect(highlighted).not.toHaveClass("font-bold")
    })

    it("serializes mentions to wire tokens on submit", async () => {
      const createComment = jest.fn().mockResolvedValue(undefined)
      mockUseCreateCaseComment.mockReturnValue({
        createComment,
        createCommentIsPending: false,
        createCommentError: null,
      })

      renderCommentSection()
      const composer = getComposer()

      typeInto(composer, "Ping @tri")
      fireEvent.click(screen.getByRole("button", { name: "Triage agent" }))
      await waitFor(() => expect(composer).toHaveValue("Ping @Triage agent "))

      fireEvent.keyDown(composer, { key: "Enter", metaKey: true })

      await waitFor(() =>
        expect(createComment).toHaveBeenCalledWith(
          expect.objectContaining({
            content: "Ping [@Triage agent](mention://agent/preset-1)",
          })
        )
      )
    })

    it("deletes a whole mention on backspace after it", async () => {
      renderCommentSection()
      const composer = getComposer()

      typeInto(composer, "Ping @tri")
      fireEvent.click(screen.getByRole("button", { name: "Triage agent" }))
      await waitFor(() => expect(composer).toHaveValue("Ping @Triage agent "))

      // Caret immediately after the mention, before the trailing space.
      typeInto(composer, "Ping @Triage agent", 18)
      fireEvent.keyDown(composer, { key: "Backspace" })

      await waitFor(() => expect(composer).toHaveValue("Ping "))
    })

    it("stays closed without the agent scopes", () => {
      mockUseScopeCheck.mockReturnValue(false)

      renderCommentSection()
      typeInto(getComposer(), "@")

      expect(screen.queryByText("Triage agent")).not.toBeInTheDocument()
      expect(getComposer()).toHaveValue("@")
    })

    it("stays closed with agent:execute but not agent:read", () => {
      // The suggestion list comes from the preset endpoint, which requires
      // agent:read; without it the request would 403.
      grantScopes(["agent:execute"])

      renderCommentSection()
      typeInto(getComposer(), "@")

      expect(mockUseScopeCheck).toHaveBeenCalledWith(
        undefined,
        ["agent:execute", "agent:read"],
        { all: true }
      )
      expect(screen.queryByText("Triage agent")).not.toBeInTheDocument()
      expect(getComposer()).toHaveValue("@")
      expect(mockUseAgentPresets).toHaveBeenCalledWith(
        "workspace-1",
        expect.objectContaining({ enabled: false })
      )
    })

    it("opens with both agent scopes granted", () => {
      grantScopes(["agent:execute", "agent:read"])

      renderCommentSection()
      typeInto(getComposer(), "@")

      expect(screen.getByText("Triage agent")).toBeInTheDocument()
      expect(mockUseAgentPresets).toHaveBeenCalledWith(
        "workspace-1",
        expect.objectContaining({ enabled: true })
      )
    })

    it.each(["case_addons", "agent_addons"])(
      "stays closed when only the %s entitlement is present",
      (entitlement) => {
        mockEntitlements([entitlement])

        renderCommentSection()
        typeInto(getComposer(), "@")

        expect(screen.queryByText("Triage agent")).not.toBeInTheDocument()
      }
    )
  })
})
