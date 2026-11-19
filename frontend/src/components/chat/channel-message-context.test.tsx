import { fireEvent, render, screen } from "@testing-library/react"
import { ChannelMessageContext } from "@/components/chat/channel-message-context"
import {
  type ChannelMessageData,
  getUserMessageData,
  isUserPrompt,
} from "@/lib/chat"

const MESSAGE: ChannelMessageData = {
  provider: "slack",
  sender: { provider_user_id: "U1", name: "Dana" },
  thread: {
    messages: [
      { author: "PagerDuty", is_bot: true, text: "Triggered: db-1 disk" },
      { author: "alice", is_bot: false, text: "on it" },
    ],
    older_omitted: true,
    later_omitted: false,
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
        parts: [{ type: "data-channel-message", data: MESSAGE }],
      })
    ).toBe(true)
  })
})
