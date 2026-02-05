"use client"

import { formatDistanceToNow } from "date-fns"
import { Check, ChevronDown, Loader2 } from "lucide-react"
import { useState } from "react"

import type { AgentSessionsListSessionsResponse } from "@/client"
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

interface ChatHistoryDropdownProps {
  chats: AgentSessionsListSessionsResponse | undefined
  isLoading: boolean
  error: unknown
  selectedChatId: string | undefined
  onSelectChat: (chatId: string) => void
}

export function ChatHistoryDropdown({
  chats,
  isLoading,
  error,
  selectedChatId,
  onSelectChat,
}: ChatHistoryDropdownProps) {
  const [open, setOpen] = useState(false)

  const handleSelect = (chatId: string) => {
    onSelectChat(chatId)
    setOpen(false)
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
          Conversations
          <ChevronDown className="ml-1 size-3" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64 p-0">
        {isLoading ? (
          <div className="flex items-center gap-2 p-3 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading chats…
          </div>
        ) : error ? (
          <div className="p-3 text-sm text-red-600">Failed to load chats</div>
        ) : (
          <Command>
            <CommandInput placeholder="Search chats..." className="h-9" />
            <CommandList className="max-h-64 overflow-y-auto">
              <CommandEmpty>No chats found.</CommandEmpty>
              <CommandGroup>
                {chats?.map((chat) => (
                  <CommandItem
                    key={chat.id}
                    value={`${chat.title} ${chat.id}`}
                    onSelect={() => handleSelect(chat.id)}
                    className="flex items-start justify-between gap-2 py-2"
                  >
                    <div className="flex min-w-0 flex-col">
                      <span className="truncate text-sm font-medium">
                        {chat.title}
                      </span>
                      <span className="text-xs text-muted-foreground">
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
