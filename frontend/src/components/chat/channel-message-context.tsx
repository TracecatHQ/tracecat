"use client"

import { ChevronRightIcon } from "lucide-react"
import type {
  EarlierThreadMessage,
  QuotedMessage as QuotedMessageData,
  QuotedMessageKind,
} from "@/client"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import type { ChannelMessageData } from "@/lib/chat"

const QUOTED_LABELS: Record<QuotedMessageKind, string> = {
  forwarded: "Forwarded from",
  quoted_reply: "Replying to",
}

/** Shown for a Slack user the message names only by ID. */
export const UNKNOWN_CHANNEL_PERSON = "Slack user"

// A Slack user or enterprise user ID after `@`, as mist's plain text leaves it.
const SLACK_MENTION = /@([UW][A-Z0-9]{6,})\b/g

/** The display name for a provider user the message refers to by ID. */
export function channelPersonName(
  message: ChannelMessageData,
  providerUserId: string,
  fallback: string = UNKNOWN_CHANNEL_PERSON
): string {
  const person = (message.people ?? []).find(
    (candidate) => candidate.provider_user_id === providerUserId
  )
  return person?.name ?? fallback
}

function quotedAuthor(
  quoted: QuotedMessageData,
  message: ChannelMessageData
): string {
  if (!quoted.author_id) return quoted.author
  // Without a lookup, keep the forward's own name, but never an ID in its place.
  const fallback =
    quoted.author && quoted.author !== quoted.author_id
      ? quoted.author
      : UNKNOWN_CHANNEL_PERSON
  return channelPersonName(message, quoted.author_id, fallback)
}

/** The sender's linked Tracecat name, else their provider display name. */
export function channelSenderName(message: ChannelMessageData): string | null {
  const person = (message.people ?? []).find(
    (candidate) =>
      candidate.provider_user_id === message.sender.provider_user_id
  )
  return person?.name ?? message.sender.name ?? null
}

/** The text with Slack user mentions shown by name instead of ID. */
export function withChannelMentionNames(
  text: string,
  message: ChannelMessageData
): string {
  if (message.provider !== "slack") return text
  return text.replace(
    SLACK_MENTION,
    (_, id: string) => `@${channelPersonName(message, id)}`
  )
}

/** The forwards and earlier thread a channel message carried. */
export function ChannelMessageContext({
  message,
}: {
  message: ChannelMessageData
}) {
  const thread = message.thread
  return (
    <div className="flex flex-col gap-2">
      {(message.quoted ?? []).map((quoted, index) => (
        <QuotedMessage key={index} quoted={quoted} message={message} />
      ))}
      {(message.quoted_omitted ?? 0) > 0 && (
        <p className="text-xs text-muted-foreground">
          {message.quoted_omitted} more not shown
        </p>
      )}
      {thread && thread.messages.length > 0 && (
        <EarlierThread
          message={message}
          messages={thread.messages}
          olderOmitted={thread.older_omitted ?? false}
          laterOmitted={thread.later_omitted ?? false}
        />
      )}
    </div>
  )
}

function QuotedMessage({
  quoted,
  message,
}: {
  quoted: QuotedMessageData
  message: ChannelMessageData
}) {
  // Stored text can be edited by workspace members, so only link https.
  const link = quoted.link?.startsWith("https://") ? quoted.link : null
  const author = quotedAuthor(quoted, message)
  return (
    <blockquote className="border-l-2 pl-3 text-sm">
      <div className="text-xs text-muted-foreground">
        {QUOTED_LABELS[quoted.kind]} {author}
        {quoted.where ? ` in #${quoted.where}` : ""}
        {link && (
          <>
            {" · "}
            <a
              href={link}
              target="_blank"
              rel="noopener noreferrer"
              className="underline underline-offset-2"
            >
              Open
            </a>
          </>
        )}
      </div>
      <p className="whitespace-pre-wrap break-words">
        {withChannelMentionNames(quoted.text, message)}
      </p>
      {quoted.is_preview && (
        <p className="text-xs text-muted-foreground">Preview only</p>
      )}
    </blockquote>
  )
}

function EarlierThread({
  message: channelMessage,
  messages,
  olderOmitted,
  laterOmitted,
}: {
  message: ChannelMessageData
  messages: EarlierThreadMessage[]
  olderOmitted: boolean
  laterOmitted: boolean
}) {
  return (
    <Collapsible>
      <CollapsibleTrigger className="group flex items-center gap-1 text-xs text-muted-foreground">
        <ChevronRightIcon className="size-3 transition-transform group-data-[state=open]:rotate-90" />
        Earlier thread · {messages.length}{" "}
        {messages.length === 1 ? "message" : "messages"}
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mt-1 flex flex-col gap-1 border-l-2 pl-3 text-sm">
          {messages.map((message, index) => (
            <p key={index} className="whitespace-pre-wrap break-words">
              <span className="font-medium">
                {message.is_bot
                  ? `${message.author} (bot)`
                  : channelPersonName(channelMessage, message.author)}
                :
              </span>{" "}
              {withChannelMentionNames(message.text, channelMessage)}
            </p>
          ))}
          {olderOmitted && (
            <p className="text-xs text-muted-foreground">
              Older replies not shown
            </p>
          )}
          {laterOmitted && (
            <p className="text-xs text-muted-foreground">
              Later replies not shown
            </p>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}
