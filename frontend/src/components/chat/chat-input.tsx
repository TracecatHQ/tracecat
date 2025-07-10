"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { ArrowUpIcon, PaperclipIcon } from "lucide-react"
import type React from "react"
import { useCallback, useEffect, useRef } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { Button } from "@/components/ui/button"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from "@/components/ui/form"
import { Textarea } from "@/components/ui/textarea"

const chatMessageSchema = z.object({
  message: z
    .string()
    .min(1, { message: "Message cannot be empty" })
    .max(2000, { message: "Message cannot be longer than 2000 characters" }),
})

type ChatMessageSchema = z.infer<typeof chatMessageSchema>

interface ChatInputProps {
  onSendMessage: (message: string) => void
  disabled?: boolean
  placeholder?: string
}

export function ChatInput({
  onSendMessage,
  disabled = false,
  placeholder = "Type your message...",
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
    [isMessageEmpty]
  )

  // Watch for message changes to auto-resize
  const messageValue = form.watch("message")
  useEffect(() => {
    adjustTextareaHeight()
  }, [messageValue, adjustTextareaHeight])

  return (
    <div className="bg-background p-3 pt-0 rounded-b-lg">
      <div className="relative flex w-full rounded-md transition-colors border hover:border-muted-foreground/40">
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(handleMessageSubmit)}
            className="flex flex-col w-full gap-2"
          >
            <FormField
              control={form.control}
              name="message"
              render={({ field }) => (
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
                      style={{ height: "60px" }} // Initial height
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <div className="flex gap-2 justify-end">
              <Button
                variant="ghost"
                size="icon"
                className="size-8 rounded-md text-muted-foreground hover:text-foreground"
                disabled
              >
                <PaperclipIcon className="size-4" />
                <span className="sr-only">Attach file</span>
              </Button>
              <Button
                variant="ghost"
                size="icon"
                type="submit"
                className="size-8 rounded-md text-muted-foreground hover:text-foreground disabled:opacity-50"
                disabled={disabled || isMessageEmpty}
              >
                <ArrowUpIcon className="size-4" />
                <span className="sr-only">Send message</span>
              </Button>
            </div>
          </form>
        </Form>
      </div>
    </div>
  )
}
