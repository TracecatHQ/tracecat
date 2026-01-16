"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { ArrowUpIcon, HammerIcon, PaperclipIcon } from "lucide-react"
import type React from "react"
import { useCallback, useEffect, useRef, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { AgentSessionEntity } from "@/client"
import { ChatToolsDialog } from "@/components/chat/chat-tools-dialog"
import { Button } from "@/components/ui/button"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from "@/components/ui/form"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"

const chatMessageSchema = z.object({
  message: z
    .string()
    .max(2000, { message: "Message cannot be longer than 2000 characters" }),
})

type ChatMessageSchema = z.infer<typeof chatMessageSchema>

interface ChatInputProps {
  onSendMessage: (message: string) => void
  disabled?: boolean
  placeholder?: string
  chatId: string
  entityType?: AgentSessionEntity
}

export function ChatInput({
  onSendMessage,
  disabled = false,
  placeholder = "Type your message...",
  chatId,
  entityType,
}: ChatInputProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const form = useForm<ChatMessageSchema>({
    resolver: zodResolver(chatMessageSchema),
    defaultValues: {
      message: "",
    },
    mode: "onSubmit",
  })

  const handleMessageSubmit = async (values: ChatMessageSchema) => {
    try {
      onSendMessage(values.message)
      form.reset({ message: "" })
    } catch (error) {
      console.error("Failed to send message:", error)
    }
  }

  const isMessageEmpty = !form.watch("message").trim()

  const adjustTextareaHeight = useCallback(() => {
    const textarea = textareaRef.current
    if (!textarea) return

    // Reset height to recalculate
    textarea.style.height = "auto"

    // Calculate new height based on scroll height
    const minHeight = 60 // min-h-[60px]
    const maxHeight = 400 // max-h-[400px]
    const scrollHeight = textarea.scrollHeight
    const newHeight = Math.min(Math.max(scrollHeight, minHeight), maxHeight)

    textarea.style.height = `${newHeight}px`

    // Handle overflow - if content exceeds max height, enable scrolling
    if (scrollHeight > maxHeight) {
      textarea.style.overflowY = "auto"
    } else {
      textarea.style.overflowY = "hidden"
    }
  }, [])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      // Only submit if Cmd+Enter or Ctrl+Enter is pressed
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        if (isMessageEmpty) {
          console.debug("Message is empty, skipping")
          return
        }
        form.handleSubmit(handleMessageSubmit)()
      }
      // Regular Enter will create newlines by default (no special handling needed)
    },
    [isMessageEmpty, form, handleMessageSubmit]
  )

  // Watch for message changes to auto-resize
  const messageValue = form.watch("message")
  useEffect(() => {
    adjustTextareaHeight()
  }, [messageValue, adjustTextareaHeight])

  return (
    <div className="bg-card p-3 pt-0 rounded-b-lg">
      <div className="relative flex flex-col w-full gap-2 rounded-md transition-colors border hover:border-muted-foreground/40">
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(handleMessageSubmit)}
            className="w-full"
          >
            <FormField
              control={form.control}
              name="message"
              render={({ field, fieldState }) => (
                <FormItem className="w-full">
                  <FormControl>
                    <Textarea
                      ref={textareaRef}
                      placeholder={placeholder}
                      className="shadow-none size-full resize-none placeholder:text-muted-foreground focus-visible:ring-0 border-none min-h-[60px]"
                      value={field.value}
                      onChange={field.onChange}
                      onKeyDown={handleKeyDown}
                      disabled={disabled}
                    />
                  </FormControl>
                  {/* Only show error message when text exceeds limit, not when empty */}
                  {fieldState.error && field.value.length > 2000 && (
                    <FormMessage />
                  )}
                </FormItem>
              )}
            />
          </form>
          <ChatControls
            sendMessage={form.handleSubmit(handleMessageSubmit)}
            sendDisabled={disabled || isMessageEmpty}
            toolsDisabled={disabled}
            chatId={chatId}
          />
        </Form>
      </div>
    </div>
  )
}

function ChatControls({
  sendMessage,
  sendDisabled = false,
  toolsDisabled = false,
  chatId,
}: {
  sendMessage: () => void
  sendDisabled?: boolean
  toolsDisabled?: boolean
  chatId: string
}) {
  const [toolsModalOpen, setToolsModalOpen] = useState(false)

  return (
    <div className="flex h-full gap-2 items-end justify-between p-1 text-muted-foreground/80">
      {/* Controls */}
      <div className="flex gap-1">
        {/* Attach file */}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="size-6 rounded-md hover:text-muted-foreground"
              disabled
            >
              <PaperclipIcon className="size-3.5" />
              <span className="sr-only">Attach file</span>
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            <p>Attach file</p>
          </TooltipContent>
        </Tooltip>

        {/* Change tools */}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              className="h-6 flex items-center p-1 gap-1 rounded-md hover:text-muted-foreground"
              onClick={() => setToolsModalOpen(true)}
              disabled={toolsDisabled}
            >
              <HammerIcon className="size-3.5" />
              <span className="text-xs">Tools</span>
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            <p>Configure tools for the agent</p>
          </TooltipContent>
        </Tooltip>
      </div>

      {/*  Actions */}
      <div className="flex gap-2">
        {/* Send message */}
        <Button
          variant="ghost"
          size="icon"
          type="submit"
          className="size-6 rounded-md hover:text-muted-foreground"
          disabled={sendDisabled}
          onClick={sendMessage}
        >
          <ArrowUpIcon className="size-3.5" />
          <span className="sr-only">Send message</span>
        </Button>
      </div>
      {/* Tools Modal */}
      <ChatToolsDialog
        open={toolsModalOpen}
        onOpenChange={setToolsModalOpen}
        chatId={chatId}
      />
    </div>
  )
}
