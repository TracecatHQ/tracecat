import { Message, MessageContent } from "@/components/ai-elements/message"
import {
  ChannelMessageContext,
  channelSenderName,
  withChannelMentionNames,
} from "@/components/chat/channel-message-context"
import { SmoothResponse } from "@/components/chat/smooth-response"
import type { ChannelMessageData } from "@/lib/chat"

/**
 * A user's message: its author, text, and a channel message's forwards and thread.
 * A channel message names its sender; any other message its recorded author.
 */
export function UserBubble({
  text,
  channelMessage,
  authorName,
}: {
  text: string
  channelMessage: ChannelMessageData | null
  authorName: string | null
}) {
  const name =
    (channelMessage && channelSenderName(channelMessage)) ?? authorName
  return (
    <Message
      from="user"
      className={name ? "flex-col items-end gap-1" : undefined}
    >
      {name && <span className="text-xs text-muted-foreground">{name}</span>}
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
    </Message>
  )
}
