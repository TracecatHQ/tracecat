import { fireEvent, render, screen } from "@testing-library/react"
import {
  ChannelMessageContext,
  withChannelMentionNames,
} from "@/components/chat/channel-message-context"
import {
  type ChannelMessageData,
  getUserMessageData,
  isUserPrompt,
} from "@/lib/chat"

const MESSAGE: ChannelMessageData = {
  provider: "slack",
  text: "triage this",
  sender: { provider_user_id: "U1", name: "Dana" },
  thread: {
    messages: [
      { author: "PagerDuty", is_bot: true, text: "Triggered: db-1 disk" },
      { author: "alice", is_bot: false, text: "on it" },
    ],
    older_omitted: true,
    later_omitted: true,
  },
  quoted: [
    {
      kind: "forwarded",
      author: "alice",
      text: "Disk at 95% on db-1",
      is_preview: false,
      where: "alerts",
      link: "https://example.slack.com/archives/C9/p1",
    },
    {
      kind: "forwarded",
      author: "bob",
      text: "Second forward",
      is_preview: false,
      link: "javascript:alert(1)",
    },
  ],
  quoted_omitted: 1,
}

describe("ChannelMessageContext", () => {
  it("shows forwards and links only https permalinks", () => {
    render(<ChannelMessageContext message={MESSAGE} />)

    expect(screen.getByText("Disk at 95% on db-1")).toBeInTheDocument()
    expect(
      screen.getByText(/Forwarded from alice in #alerts/)
    ).toBeInTheDocument()
    const links = screen.getAllByRole("link")
    expect(links).toHaveLength(1)
    expect(links[0]).toHaveAttribute(
      "href",
      "https://example.slack.com/archives/C9/p1"
    )
    expect(screen.getByText("1 more not shown")).toBeInTheDocument()
  })

  it("collapses the earlier thread until opened", () => {
    render(<ChannelMessageContext message={MESSAGE} />)

    expect(screen.queryByText("Triggered: db-1 disk")).not.toBeInTheDocument()
    fireEvent.click(screen.getByText(/Earlier thread · 2 messages/))
    expect(screen.getByText("Triggered: db-1 disk")).toBeInTheDocument()
    expect(screen.getByText("Older replies not shown")).toBeInTheDocument()
    expect(screen.getByText("Later replies not shown")).toBeInTheDocument()
  })
})

describe("ChannelMessageContext names", () => {
  const NAMED: ChannelMessageData = {
    provider: "slack",
    text: "ask @U0C9F646K8B and @U0UNKNOWN1",
    sender: { provider_user_id: "U1", name: "Dana" },
    thread: {
      messages: [
        { author: "U0C9F646K8B", is_bot: false, text: "cc @U0AAAAAAA1" },
        { author: "U0UNKNOWN1", is_bot: false, text: "ok" },
      ],
    },
    quoted: [
      {
        kind: "forwarded",
        author: "sam",
        author_id: "U0AAAAAAA1",
        text: "fwd",
        is_preview: false,
      },
    ],
    people: [
      { provider_user_id: "U0C9F646K8B", name: "Ada Lovelace" },
      { provider_user_id: "U0AAAAAAA1", name: "Sam Doe" },
    ],
  }

  it("names earlier-thread and quoted authors, never their IDs", () => {
    const { container } = render(<ChannelMessageContext message={NAMED} />)
    fireEvent.click(screen.getByText(/Earlier thread · 2 messages/))

    expect(screen.getByText("Ada Lovelace:")).toBeInTheDocument()
    expect(screen.getByText("Slack user:")).toBeInTheDocument()
    expect(container.textContent).toContain("cc @Sam Doe")
    expect(container.textContent).toContain("Forwarded from Sam Doe")
    expect(container.textContent).not.toMatch(/U0C9F646K8B|U0UNKNOWN1/)
  })

  it("keeps a forward's own name when the person was not looked up", () => {
    const forward = NAMED.quoted?.[0]
    if (!forward) throw new Error("NAMED has a forward")
    const unnamed: ChannelMessageData = {
      ...NAMED,
      quoted: [
        forward,
        { ...forward, author: "U0BBBBBBB1", author_id: "U0BBBBBBB1" },
      ],
      people: [],
    }
    const { container } = render(<ChannelMessageContext message={unnamed} />)

    expect(container.textContent).toContain("Forwarded from sam")
    expect(container.textContent).toContain("Forwarded from Slack user")
    expect(container.textContent).not.toMatch(/U0BBBBBBB1/)
  })

  it("highlights linked members and mutes unlinked Slack names", () => {
    const linked: ChannelMessageData = {
      ...NAMED,
      people: [
        { provider_user_id: "U0C9F646K8B", name: "Ada Lovelace", linked: true },
        { provider_user_id: "U0AAAAAAA1", name: "Sam Doe" },
      ],
    }
    render(<ChannelMessageContext message={linked} />)
    fireEvent.click(screen.getByText(/Earlier thread · 2 messages/))

    expect(screen.getByText("Ada Lovelace:")).toHaveClass(
      "font-medium",
      "text-foreground"
    )
    expect(screen.getByText("@Sam Doe")).toHaveClass("text-muted-foreground")
    expect(screen.getByText("Sam Doe")).toHaveClass("text-muted-foreground")
  })

  it("shows mentions by name, else as a Slack user", () => {
    expect(withChannelMentionNames(NAMED.text, NAMED)).toBe(
      "ask @Ada Lovelace and @Slack user"
    )
  })
})

describe("channel message parts", () => {
  it("reads a steered channel message's context", () => {
    expect(
      getUserMessageData({
        id: "s1",
        text: "and db-2",
        channel_message: MESSAGE,
      })
    ).toEqual({ id: "s1", text: "and db-2", channelMessage: MESSAGE })
    expect(getUserMessageData({ id: null, text: "plain" })).toEqual({
      id: null,
      text: "plain",
      channelMessage: null,
    })
  })

  it("treats a forward-only channel message as a prompt", () => {
    expect(
      isUserPrompt({
        role: "user",
        parts: [
          { type: "data-channel-message", data: { ...MESSAGE, text: "" } },
        ],
      })
    ).toBe(true)
  })
})
