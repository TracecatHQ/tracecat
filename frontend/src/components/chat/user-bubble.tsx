import { createContext, useContext } from "react"
import type { InboxChannelThread } from "@/client"
import {
  Message,
  MessageAvatar,
  MessageContent,
} from "@/components/ai-elements/message"
import {
  ChannelMessageContext,
  channelPersonClassName,
  channelSender,
  withChannelMentionNames,
} from "@/components/chat/channel-message-context"
import { SmoothResponse } from "@/components/chat/smooth-response"
import { SlackIcon } from "@/components/icons"
import { useChannelThreadChannel } from "@/hooks/use-agent-channel-bots"
import type { ChannelMessageData, MessageActor } from "@/lib/chat"
import { useOptionalWorkspaceId } from "@/providers/workspace-id"

/** The session's linked channel thread, which names where Slack messages came from. */
export const SessionChannelThreadContext =
  createContext<InboxChannelThread | null>(null)

/**
 * A user's message: its author, text, and a channel message's forwards and thread.
 * The recorded author names it; else a channel message's envelope sender.
 */
export function UserBubble({
  text,
  channelMessage,
  author,
  own = false,
}: {
  text: string
  channelMessage: ChannelMessageData | null
  author: MessageActor | null
  /** The viewer wrote this message. */
  own?: boolean
}) {
  // A recorded author (a linked sender) outranks the envelope's label.
  const actor = channelMessage
    ? author
      ? { ...author, fromSlack: channelMessage.provider === "slack" }
      : channelActor(channelMessage)
    : author
  const content = (
    <MessageContent variant="flat">
      {text && (
        <SmoothResponse
          text={
            channelMessage
              ? withChannelMentionNames(text, channelMessage)
              : text
          }
          animate={false}
        />
      )}
      {channelMessage && <ChannelMessageContext message={channelMessage} />}
    </MessageContent>
  )
  if (own) {
    const fromSlack = channelMessage?.provider === "slack"
    return (
      <Message from="user" className="flex-col items-end gap-1">
        {fromSlack && <FromSlackLine />}
        {content}
      </Message>
    )
  }
  if (!actor) {
    return <Message from="user">{content}</Message>
  }
  return (
    <Message from="user" className="justify-start">
      <div className="flex min-w-0 flex-1 items-start gap-2.5">
        <MessageAvatar
          name={actor.name}
          aria-label={actor.name}
          badge={
            actor.fromSlack ? (
              <SlackIcon className="size-2.5" aria-hidden="true" />
            ) : undefined
          }
        />
        <div className="flex min-w-0 flex-1 flex-col items-start gap-1">
          <span className="pt-1.5 text-xs text-muted-foreground">
            <span className={channelPersonClassName(actor.linked)}>
              {actor.name}
            </span>
          </span>
          {content}
        </div>
      </div>
    </Message>
  )
}

/** A channel message's sender, labelled by where they wrote from. */
function channelActor(message: ChannelMessageData): MessageActor {
  const sender = channelSender(message)
  return {
    name: sender.name,
    fromSlack: message.provider === "slack",
    linked: sender.linked,
  }
}

/** "From #channel" above the viewer's own Slack message; "From Slack" when unnamed. */
function FromSlackLine() {
  const thread = useContext(SessionChannelThreadContext)
  const workspaceId = useOptionalWorkspaceId()
  return (
    <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
      <SlackIcon className="size-3.5" aria-hidden="true" />
      {thread?.provider === "slack" && workspaceId ? (
        <ChannelName workspaceId={workspaceId} thread={thread} />
      ) : (
        "From Slack"
      )}
    </span>
  )
}

function ChannelName({
  workspaceId,
  thread,
}: {
  workspaceId: string
  thread: InboxChannelThread
}) {
  const channel = useChannelThreadChannel(workspaceId, thread)
  return channel ? `From #${channel.name}` : "From Slack"
}
