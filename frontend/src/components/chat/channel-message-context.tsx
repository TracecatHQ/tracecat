"use client"

import { ChevronRightIcon } from "lucide-react"
import type { ReactNode } from "react"
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

/** A person the message names, and whether the name is a linked Tracecat member's. */
export type ChannelPersonLabel = { name: string; linked: boolean }

/** A provider user the message refers to by ID, by name. */
export function channelPerson(
  message: ChannelMessageData,
  providerUserId: string,
  fallback: string = UNKNOWN_CHANNEL_PERSON
): ChannelPersonLabel {
  const person = (message.people ?? []).find(
    (candidate) => candidate.provider_user_id === providerUserId
  )
  if (!person) return { name: fallback, linked: false }
  return { name: person.name, linked: person.linked ?? false }
}

/** The display name for a provider user the message refers to by ID. */
export function channelPersonName(
  message: ChannelMessageData,
  providerUserId: string,
  fallback: string = UNKNOWN_CHANNEL_PERSON
): string {
  return channelPerson(message, providerUserId, fallback).name
}

function quotedAuthor(
  quoted: QuotedMessageData,
  message: ChannelMessageData
): ChannelPersonLabel | null {
  if (!quoted.author_id) return null
  // Without a lookup, keep the forward's own name, but never an ID in its place.
  const fallback =
    quoted.author && quoted.author !== quoted.author_id
      ? quoted.author
      : UNKNOWN_CHANNEL_PERSON
  return channelPerson(message, quoted.author_id, fallback)
}

/** Who sent the message: their linked Tracecat name, else their provider display name. */
export function channelSender(message: ChannelMessageData): ChannelPersonLabel {
  const person = (message.people ?? []).find(
    (candidate) =>
      candidate.provider_user_id === message.sender.provider_user_id
  )
  if (person) return { name: person.name, linked: person.linked ?? false }
  // A nameless sender still reads as a channel person, never the turn's web author.
  return { name: message.sender.name || UNKNOWN_CHANNEL_PERSON, linked: false }
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

/** Linked members read as highlighted; unlinked provider names stay muted. */
export function channelPersonClassName(linked: boolean): string {
  return linked ? "font-medium text-foreground" : "text-muted-foreground"
}

/** The text with Slack user mentions as styled names instead of IDs. */
export function ChannelMentionText({
  text,
  message,
}: {
  text: string
  message: ChannelMessageData
}) {
  if (message.provider !== "slack") return <>{text}</>
  const nodes: ReactNode[] = []
  let last = 0
  for (const match of text.matchAll(SLACK_MENTION)) {
    const index = match.index ?? 0
    nodes.push(text.slice(last, index))
    const person = channelPerson(message, match[1])
    nodes.push(
      <span key={index} className={channelPersonClassName(person.linked)}>
        @{person.name}
      </span>
    )
    last = index + match[0].length
  }
  nodes.push(text.slice(last))
  return <>{nodes}</>
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
        {QUOTED_LABELS[quoted.kind]}{" "}
        {author ? (
          <span className={channelPersonClassName(author.linked)}>
            {author.name}
          </span>
        ) : (
          quoted.author
        )}
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
        <ChannelMentionText text={quoted.text} message={message} />
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
              <EarlierThreadAuthor
                message={channelMessage}
                author={message.author}
                isBot={message.is_bot ?? false}
              />{" "}
              <ChannelMentionText
                text={message.text}
                message={channelMessage}
              />
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

function EarlierThreadAuthor({
  message,
  author,
  isBot,
}: {
  message: ChannelMessageData
  author: string
  isBot: boolean
}) {
  if (isBot) {
    return <span className="font-medium">{author} (bot):</span>
  }
  const person = channelPerson(message, author)
  return (
    <span className={channelPersonClassName(person.linked)}>
      {person.name}:
    </span>
  )
}
