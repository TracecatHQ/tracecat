"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { ArrowUpIcon, PaperclipIcon } from "lucide-react"
import type React from "react"
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
  const form = useForm<ChatMessageSchema>({
    resolver: zodResolver(chatMessageSchema),
    defaultValues: {
      message: "",
    },
    mode: "onSubmit",
  })

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Only submit if Cmd+Enter or Ctrl+Enter is pressed
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      form.handleSubmit(handleMessageSubmit)()
    }
    // Regular Enter will create newlines by default (no special handling needed)
  }

  const handleMessageSubmit = async (values: ChatMessageSchema) => {
    try {
      onSendMessage(values.message)
      form.reset({ message: "" })
    } catch (error) {
      console.error("Failed to send message:", error)
    }
  }

  const isMessageEmpty = !form.watch("message").trim()

  return (
    <div className="border-t bg-background p-4">
      <div className="relative flex w-full">
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(handleMessageSubmit)}
            className="flex w-full space-x-2"
          >
            <FormField
              control={form.control}
              name="message"
              render={({ field }) => (
                <FormItem className="w-full">
                  <FormControl>
                    <Textarea
                      placeholder={placeholder}
                      className="min-h-[80px] w-full resize-none rounded-md pr-16 placeholder:text-muted-foreground focus-visible:ring-muted-foreground/30"
                      value={field.value}
                      onChange={field.onChange}
                      onKeyDown={handleKeyDown}
                      disabled={disabled}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <div className="absolute bottom-3 right-3 flex gap-2">
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
