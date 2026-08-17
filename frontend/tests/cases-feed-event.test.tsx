/**
 * @jest-environment jsdom
 */

import { render, screen } from "@testing-library/react"
import type { ReactNode } from "react"
import type {
  CommentCreatedEventRead,
  CommentReplyDeletedEventRead,
  CommentReplyUpdatedEventRead,
  FieldChangedEventRead,
} from "@/client"
import { CASE_EVENT_SUGGESTIONS } from "@/components/builder/panel/case-event-suggestions"
import {
  CASE_EVENT_OPTIONS,
  getCaseEventOption,
} from "@/components/cases/case-duration-options"
import {
  CommentCreatedEvent,
  CommentReplyDeletedEvent,
  CommentReplyUpdatedEvent,
  FieldsChangedEvent,
} from "@/components/cases/cases-feed-event"
import { User } from "@/lib/auth"

jest.mock("@/components/cases/case-panel-common", () => ({
  UserHoverCard: ({ children }: { user: User; children?: ReactNode }) => (
    <>{children}</>
  ),
}))

const actor = new User({
  id: "user-1",
  email: "owner@example.com",
  role: "admin",
  first_name: "Owner",
  last_name: "One",
  settings: {},
})

function buildCommentCreatedEvent(): CommentCreatedEventRead {
  return {
    type: "comment_created",
    comment_id: "comment-1",
    parent_id: null,
    thread_root_id: "comment-1",
    created_at: "2026-03-08T00:00:00Z",
    user_id: "user-1",
    wf_exec_id: null,
  }
}

function buildReplyUpdatedEvent(): CommentReplyUpdatedEventRead {
  return {
    type: "comment_reply_updated",
    comment_id: "comment-2",
    parent_id: "comment-1",
    thread_root_id: "comment-1",
    created_at: "2026-03-08T00:00:00Z",
    user_id: "user-1",
    wf_exec_id: null,
  }
}

function buildReplyDeletedEvent(): CommentReplyDeletedEventRead {
  return {
    type: "comment_reply_deleted",
    comment_id: "comment-2",
    parent_id: "comment-1",
    thread_root_id: "comment-1",
    delete_mode: "hard",
    created_at: "2026-03-08T00:00:00Z",
    user_id: "user-1",
    wf_exec_id: null,
  }
}

describe("case feed comment events", () => {
  it("renders comment activity copy in the feed", () => {
    render(
      <div>
        <CommentCreatedEvent event={buildCommentCreatedEvent()} actor={actor} />
        <CommentReplyUpdatedEvent
          event={buildReplyUpdatedEvent()}
          actor={actor}
        />
        <CommentReplyDeletedEvent
          event={buildReplyDeletedEvent()}
          actor={actor}
        />
      </div>
    )

    expect(screen.getByText("created a comment")).toBeInTheDocument()
    expect(screen.getByText("edited a reply")).toBeInTheDocument()
    expect(screen.getByText("deleted a reply")).toBeInTheDocument()
  })

  it("offers comment events in both trigger suggestions and duration options", () => {
    const suggestionValues = new Set(
      CASE_EVENT_SUGGESTIONS.map(({ value }) => value)
    )
    const durationValues = new Set(CASE_EVENT_OPTIONS.map(({ value }) => value))

    expect(suggestionValues).toContain("comment_created")
    expect(suggestionValues).toContain("comment_reply_deleted")
    expect(durationValues).toContain("comment_created")
    expect(durationValues).toContain("comment_reply_deleted")
    expect(getCaseEventOption("comment_reply_updated").label).toBe(
      "Comment Reply Updated"
    )
  })
})

describe("case feed field events", () => {
  it("shows display names while retaining stable references in event data", () => {
    const event: FieldChangedEventRead = {
      type: "fields_changed",
      changes: [
        {
          field: "analyst_verdict_v2",
          old: "Investigating",
          new: "Resolved",
        },
        {
          field: "deleted_field",
          old: null,
          new: "Still readable",
        },
      ],
      created_at: "2026-03-08T00:00:00Z",
      user_id: "user-1",
      wf_exec_id: null,
    }

    render(
      <FieldsChangedEvent
        event={event}
        actor={actor}
        caseFieldDisplayNameById={
          new Map([["analyst_verdict_v2", "Final determination"]])
        }
      />
    )

    expect(screen.getByText("Final determination")).toBeInTheDocument()
    expect(screen.queryByText("analyst_verdict_v2")).not.toBeInTheDocument()
    expect(screen.getByText("deleted_field")).toBeInTheDocument()
  })
})
