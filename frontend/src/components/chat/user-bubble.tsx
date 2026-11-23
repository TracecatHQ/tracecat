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
import type { ChannelMessageData, MessageActor } from "@/lib/chat"

/**
 * A user's message: its author, text, and a channel message's forwards and thread.
 * A channel message names its sender; any other message its recorded author.
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
  const actor = (channelMessage && channelActor(channelMessage)) ?? author
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
  // An unlinked channel sender is never the viewer, whatever the turn's author.
  const viewerWrote =
    own && (!channelMessage || channelSender(channelMessage)?.linked === true)
  if (viewerWrote) {
    // No channel name reaches the chat yet, so the line names the provider.
    const fromSlack = channelMessage?.provider === "slack"
    return (
      <Message from="user" className="flex-col items-end gap-1">
        {fromSlack && (
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <SlackIcon className="size-3.5" aria-hidden="true" />
            From Slack
          </span>
        )}
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
function channelActor(message: ChannelMessageData): MessageActor | null {
  const sender = channelSender(message)
  if (!sender) return null
  return {
    name: sender.name,
    fromSlack: message.provider === "slack",
    linked: sender.linked,
  }
}
