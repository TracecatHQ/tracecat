"use client"

import type { ReactNode } from "react"
import type {
  EarlierThreadMessage,
  QuotedMessage as QuotedMessageData,
  QuotedMessageKind,
} from "@/client"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { ChannelMessageData } from "@/lib/chat"

const QUOTED_LABELS: Record<QuotedMessageKind, string> = {
  forwarded: "Forwarded from",
  quoted_reply: "Replying to",
}

/** Shown for a Slack user the message names only by ID. */
export const UNKNOWN_CHANNEL_PERSON = "Slack user"

// A Slack user or enterprise user ID after `@`, as mist's plain text leaves it.
const SLACK_MENTION = /@([UW][A-Z0-9]{6,})\b/g
// An https URL; trailing punctuation stays text. Stored text is editable, so no other scheme.
const URL_PATTERN = /https:\/\/[^\s<>]*[^\s<>.,;:!?)\]'"]/g
const SLACK_PERMALINK_PATH = /^\/archives\/([A-Z0-9]+)\/p\d+/
// A fenced block (unclosed runs to the end) or inline code span; links inside stay as typed.
const MARKDOWN_CODE = /```[\s\S]*?(?:```|$)|`[^`\n]*`/
const CODE_OR_URL = new RegExp(
  `${MARKDOWN_CODE.source}|${URL_PATTERN.source}`,
  "g"
)

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

/** The channel a Slack permalink points to, or null for any other URL. */
function slackPermalinkChannel(url: URL): string | null {
  if (!url.hostname.endsWith(".slack.com")) return null
  return url.pathname.match(SLACK_PERMALINK_PATH)?.[1] ?? null
}

function parsedUrl(url: string): URL | null {
  try {
    return new URL(url)
  } catch {
    return null
  }
}

/** A short label for a URL: "#alerts message ↗" for a permalink, else its host. */
export function channelLinkLabel(
  url: string,
  message: ChannelMessageData
): string {
  const parsed = parsedUrl(url)
  if (!parsed) return url
  const channelId = slackPermalinkChannel(parsed)
  if (!channelId) return `${parsed.hostname} ↗`
  // A forward from the same channel names it.
  const name = message.quoted?.find((quoted) => {
    const link = quoted.link ? parsedUrl(quoted.link) : null
    return quoted.where && link && slackPermalinkChannel(link) === channelId
  })?.where
  const kind = parsed.searchParams.has("thread_ts") ? "thread" : "message"
  return `${name ? `#${name}` : "Slack"} ${kind} ↗`
}

/** Markdown text with URLs as short-labelled links, the full URL as their title. */
export function withChannelLinkLabels(
  text: string,
  message: ChannelMessageData
): string {
  return text.replace(CODE_OR_URL, (url, offset: number) => {
    // Code, or already a markdown link's target.
    if (url.startsWith("`") || text.slice(offset - 2, offset) === "](")
      return url
    const href = url.replace(/\(/g, "%28").replace(/\)/g, "%29")
    return `[${channelLinkLabel(url, message)}](${href} "${href.replace(/"/g, "%22")}")`
  })
}

function ChannelLink({
  url,
  message,
}: {
  url: string
  message: ChannelMessageData
}) {
  const parsed = parsedUrl(url)
  const anchor = (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="underline underline-offset-2"
    >
      {channelLinkLabel(url, message)}
    </a>
  )
  if (!parsed || slackPermalinkChannel(parsed)) return anchor
  return (
    <Tooltip>
      <TooltipTrigger asChild>{anchor}</TooltipTrigger>
      <TooltipContent className="max-w-sm break-all">{url}</TooltipContent>
    </Tooltip>
  )
}

/** The text with Slack user mentions as styled names and URLs as short links. */
export function ChannelMentionText({
  text,
  message,
}: {
  text: string
  message: ChannelMessageData
}) {
  const nodes: ReactNode[] = []
  let last = 0
  for (const match of text.matchAll(URL_PATTERN)) {
    const index = match.index ?? 0
    nodes.push(...mentionNodes(text.slice(last, index), message, last))
    nodes.push(<ChannelLink key={index} url={match[0]} message={message} />)
    last = index + match[0].length
  }
  nodes.push(...mentionNodes(text.slice(last), message, last))
  return <>{nodes}</>
}

function mentionNodes(
  text: string,
  message: ChannelMessageData,
  offset: number
): ReactNode[] {
  if (message.provider !== "slack") return [text]
  const nodes: ReactNode[] = []
  let last = 0
  for (const match of text.matchAll(SLACK_MENTION)) {
    const index = match.index ?? 0
    nodes.push(text.slice(last, index))
    const person = channelPerson(message, match[1])
    nodes.push(
      <span
        key={offset + index}
        className={channelPersonClassName(person.linked)}
      >
        @{person.name}
      </span>
    )
    last = index + match[0].length
  }
  nodes.push(text.slice(last))
  return nodes
}

/** The earlier-thread lines that mention the bot; each reads as its own message. */
export function botMentions(
  message: ChannelMessageData,
  botUserId: string
): EarlierThreadMessage[] {
  return (message.thread?.messages ?? []).filter((line) =>
    Array.from(line.text.matchAll(SLACK_MENTION)).some(
      (match) => match[1] === botUserId
    )
  )
}

/** The forwards a channel message carried; its earlier thread is agent context only. */
export function ChannelMessageContext({
  message,
}: {
  message: ChannelMessageData
}) {
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
