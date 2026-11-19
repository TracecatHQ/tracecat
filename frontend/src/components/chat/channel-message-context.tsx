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
        <QuotedMessage key={index} quoted={quoted} />
      ))}
      {(message.quoted_omitted ?? 0) > 0 && (
        <p className="text-xs text-muted-foreground">
          {message.quoted_omitted} more not shown
        </p>
      )}
      {thread && thread.messages.length > 0 && (
        <EarlierThread
          messages={thread.messages}
          olderOmitted={thread.older_omitted ?? false}
        />
      )}
    </div>
  )
}

function QuotedMessage({ quoted }: { quoted: QuotedMessageData }) {
  // Stored text can be edited by workspace members, so only link https.
  const link = quoted.link?.startsWith("https://") ? quoted.link : null
  return (
    <blockquote className="border-l-2 pl-3 text-sm">
      <div className="text-xs text-muted-foreground">
        {QUOTED_LABELS[quoted.kind]} {quoted.author}
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
      <p className="whitespace-pre-wrap break-words">{quoted.text}</p>
      {quoted.is_preview && (
        <p className="text-xs text-muted-foreground">Preview only</p>
      )}
    </blockquote>
  )
}

function EarlierThread({
  messages,
  olderOmitted,
}: {
  messages: EarlierThreadMessage[]
  olderOmitted: boolean
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
                {message.author}
                {message.is_bot ? " (bot)" : ""}:
              </span>{" "}
              {message.text}
            </p>
          ))}
          {olderOmitted && (
            <p className="text-xs text-muted-foreground">
              Older replies not shown
            </p>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}
