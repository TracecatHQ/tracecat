import { render, screen } from "@testing-library/react"
import {
  ChannelMessageContext,
  channelMarkdownComponents,
  withChannelMentionNames,
} from "@/components/chat/channel-message-context"
import { TooltipProvider } from "@/components/ui/tooltip"
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

  it("never renders the earlier thread", () => {
    const { container } = render(<ChannelMessageContext message={MESSAGE} />)

    expect(container.textContent).not.toMatch(/Earlier thread|db-1 disk|on it/)
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

  it("names quoted authors, never their IDs", () => {
    const { container } = render(<ChannelMessageContext message={NAMED} />)

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

  it("mutes unlinked Slack names", () => {
    const linked: ChannelMessageData = {
      ...NAMED,
      people: [
        { provider_user_id: "U0C9F646K8B", name: "Ada Lovelace", linked: true },
        { provider_user_id: "U0AAAAAAA1", name: "Sam Doe" },
      ],
    }
    render(<ChannelMessageContext message={linked} />)

    expect(screen.getByText("Sam Doe")).toHaveClass("text-muted-foreground")
  })

  it("shows mentions by name, else as a Slack user", () => {
    expect(withChannelMentionNames(NAMED.text, NAMED)).toBe(
      "ask @Ada Lovelace and @Slack user"
    )
  })
})

describe("ChannelMessageContext links", () => {
  const PERMALINK = "https://example.slack.com/archives/C9/p1700000000000100"
  const LINKED: ChannelMessageData = {
    provider: "slack",
    text: "see these",
    sender: { provider_user_id: "U1", name: "Dana" },
    quoted: [
      {
        kind: "forwarded",
        author: "alice",
        text: `same ${PERMALINK}, other https://example.slack.com/archives/C8/p2?thread_ts=1.2&cid=C8 and https://docs.example.com/runbooks/disk-full-on-db-hosts.`,
        is_preview: false,
        where: "alerts",
        link: PERMALINK,
      },
    ],
  }

  it("shows URLs as short labels, never in full", () => {
    const { container } = render(
      <TooltipProvider>
        <ChannelMessageContext message={LINKED} />
      </TooltipProvider>
    )
    const body = container.querySelector("blockquote p")

    expect(body?.textContent).toBe(
      "same #alerts message ↗, other Slack thread ↗ and docs.example.com ↗."
    )
    expect(
      screen.getByRole("link", { name: "docs.example.com ↗" })
    ).toHaveAttribute(
      "href",
      "https://docs.example.com/runbooks/disk-full-on-db-hosts"
    )
    // The forward header keeps its own link.
    expect(screen.getByRole("link", { name: "Open" })).toHaveAttribute(
      "href",
      PERMALINK
    )
  })

  it("keeps a closing paren the URL opened", () => {
    const url = "https://en.example.org/wiki/Alert_(monitoring)"
    render(
      <TooltipProvider>
        <ChannelMessageContext
          message={{
            ...LINKED,
            quoted: [{ ...LINKED.quoted![0], text: `(see ${url})` }],
          }}
        />
      </TooltipProvider>
    )
    expect(
      screen.getByRole("link", { name: "en.example.org ↗" })
    ).toHaveAttribute("href", url)
  })

  it("leaves URLs in a quoted body's code as typed", () => {
    const text = "run `curl https://a.example/x` then https://b.example/y"
    render(
      <TooltipProvider>
        <ChannelMessageContext
          message={{ ...LINKED, quoted: [{ ...LINKED.quoted![0], text }] }}
        />
      </TooltipProvider>
    )
    expect(screen.getByText(/curl https:\/\/a\.example\/x/)).toBeVisible()
    expect(screen.getByRole("link", { name: "b.example ↗" })).toBeVisible()
  })

  it("labels an autolinked URL in the message's own markdown", () => {
    const { a: Link } = channelMarkdownComponents(LINKED)
    render(
      <TooltipProvider>
        <Link href={PERMALINK}>{PERMALINK}</Link>
        <Link href="https://a.example/">https://a.example</Link>
      </TooltipProvider>
    )
    expect(
      screen.getByRole("link", { name: "#alerts message ↗" })
    ).toHaveAttribute("href", PERMALINK)
    expect(screen.getByRole("link", { name: "a.example ↗" })).toBeVisible()
  })

  it("keeps a written link's own text", () => {
    const { a: Link } = channelMarkdownComponents(LINKED)
    render(
      <>
        <Link href="https://a.example/">x</Link>
        <Link href="https://b.example/">https://a.example</Link>
      </>
    )
    expect(screen.getByRole("link", { name: "x" })).toBeVisible()
    expect(
      screen.getByRole("link", { name: "https://a.example" })
    ).toHaveAttribute("href", "https://b.example/")
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
