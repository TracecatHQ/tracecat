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
}: {
  text: string
  channelMessage: ChannelMessageData | null
  author: MessageActor | null
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
  if (!actor) {
    return <Message from="user">{content}</Message>
  }
  return (
    <Message from="user" className="flex-col items-end gap-1">
      <span className="text-xs text-muted-foreground">
        <span className={channelPersonClassName(actor.linked)}>
          {actor.name}
        </span>
        {actor.fromSlack ? " · from Slack" : null}
      </span>
      <div className="flex w-full items-start justify-end gap-2.5">
        {content}
        <MessageAvatar
          name={actor.name}
          aria-label={`${actor.name}, from ${actor.fromSlack ? "Slack" : "Tracecat"}`}
          badge={
            actor.fromSlack ? (
              <SlackIcon className="size-2.5" aria-hidden="true" />
            ) : undefined
          }
        />
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
