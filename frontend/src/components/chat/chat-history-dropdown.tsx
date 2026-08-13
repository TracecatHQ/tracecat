"use client"

import { formatDistanceToNow } from "date-fns"
import { Check, ChevronDown, Loader2 } from "lucide-react"
import { useState } from "react"

import type { AgentSessionsListSessionsResponse } from "@/client"
import { ChatLastErrorIndicator } from "@/components/chat/chat-last-error-indicator"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { ToggleTabs } from "@/components/ui/toggle-tabs"
import { useWorkspaceMembers } from "@/hooks/use-workspace"
import { getDisplayName } from "@/lib/auth"

export type ChatHistoryScope = "team" | "mine"

const COMMENT_AGENT_SESSION_BADGE = "From comment"

function isCommentAgentSession(
  chat: AgentSessionsListSessionsResponse[number]
): boolean {
  return (
    "created_by" in chat &&
    chat.entity_type === "case" &&
    chat.agent_preset_id !== null &&
    chat.channel_context?.session_origin === "case_comment"
  )
}

interface ChatHistoryDropdownProps {
  chats: AgentSessionsListSessionsResponse | undefined
  isLoading: boolean
  error: unknown
  selectedChatId: string | undefined
  onSelectChat: (chatId: string) => void
  workspaceId: string
  scope: ChatHistoryScope
  onScopeChange: (scope: ChatHistoryScope) => void
  align?: "start" | "center" | "end"
}

export function ChatHistoryDropdown({
  chats,
  isLoading,
  error,
  selectedChatId,
  onSelectChat,
  workspaceId,
  scope,
  onScopeChange,
  align = "start",
}: ChatHistoryDropdownProps) {
  const [open, setOpen] = useState(false)
  const { members } = useWorkspaceMembers(workspaceId, { enabled: open })

  function creatorLabel(createdBy: string | null): string {
    if (!createdBy) {
      return "System"
    }
    const member = members?.find((candidate) => candidate.user_id === createdBy)
    return member ? getDisplayName(member) : "Teammate"
  }

  const handleSelect = (chatId: string) => {
    onSelectChat(chatId)
    setOpen(false)
  }

  // Hide the selector entirely when there is no chat history — an empty
  // dropdown is just noise. Still render while loading or on error so those
  // states aren't silently swallowed.
  if (!isLoading && !error && (chats?.length ?? 0) === 0 && scope === "team") {
    return null
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          size="sm"
          variant="ghost"
          className="px-2"
          role="combobox"
          aria-expanded={open}
        >
          Chats
          <ChevronDown className="ml-1 size-3" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align={align} className="w-64 p-0">
        <div className="flex items-center justify-between border-b px-3 py-2">
          <span className="text-xs text-muted-foreground">Show chats</span>
          <ToggleTabs<ChatHistoryScope>
            value={scope}
            onValueChange={onScopeChange}
            size="sm"
            showTooltips={false}
            options={[
              { value: "team", content: "Team" },
              { value: "mine", content: "Mine" },
            ]}
          />
        </div>
        {isLoading ? (
          <div className="flex items-center gap-2 p-3 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading chats…
          </div>
        ) : error ? (
          <div className="p-3 text-sm text-red-600">Failed to load chats</div>
        ) : (
          <Command
            filter={(value, search) => {
              const chat = chats?.find((item) => item.id === value)
              if (!chat) {
                return 0
              }

              const normalizedSearch = search.trim().toLowerCase()
              if (!normalizedSearch) {
                return 1
              }

              const createdBy =
                "created_by" in chat ? chat.created_by : chat.user_id
              const origin = isCommentAgentSession(chat)
                ? COMMENT_AGENT_SESSION_BADGE
                : ""
              return `${chat.title} ${chat.id} ${creatorLabel(createdBy)} ${origin}`
                .toLowerCase()
                .includes(normalizedSearch)
                ? 1
                : 0
            }}
          >
            <CommandInput
              placeholder="Search chats..."
              className="h-8 text-xs"
            />
            <CommandList className="max-h-64 overflow-y-auto">
              <CommandEmpty className="m-1 rounded-sm bg-muted/40 px-3 py-4 text-center text-xs text-muted-foreground">
                No chats found.
              </CommandEmpty>
              <CommandGroup>
                {chats?.map((chat) => (
                  <CommandItem
                    key={chat.id}
                    value={chat.id}
                    onSelect={() => handleSelect(chat.id)}
                    className="flex items-start justify-between gap-2 py-2 text-xs"
                  >
                    <div className="flex min-w-0 flex-col">
                      <div className="flex items-center gap-1.5">
                        <span className="truncate font-medium">
                          {chat.title}
                        </span>
                        <ChatLastErrorIndicator session={chat} />
                        {isCommentAgentSession(chat) ? (
                          <Badge
                            variant="secondary"
                            className="shrink-0 px-1.5 py-0 text-[10px] font-normal"
                          >
                            {COMMENT_AGENT_SESSION_BADGE}
                          </Badge>
                        ) : null}
                        {chat.is_readonly ? (
                          <Badge
                            variant="outline"
                            className="shrink-0 px-1.5 py-0 text-[10px] font-normal text-muted-foreground"
                          >
                            Read only
                          </Badge>
                        ) : null}
                      </div>
                      <span className="text-xs text-muted-foreground">
                        {creatorLabel(
                          "created_by" in chat ? chat.created_by : chat.user_id
                        )}{" "}
                        ·{" "}
                        {formatDistanceToNow(new Date(chat.created_at), {
                          addSuffix: true,
                        })}
                      </span>
                    </div>
                    {selectedChatId === chat.id ? (
                      <Check className="mt-1 size-4 shrink-0" />
                    ) : null}
                  </CommandItem>
                ))}
              </CommandGroup>
            </CommandList>
          </Command>
        )}
      </PopoverContent>
    </Popover>
  )
}
