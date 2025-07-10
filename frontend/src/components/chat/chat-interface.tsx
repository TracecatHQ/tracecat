"use client"

import { Bot, MessageSquare } from "lucide-react"
import { useEffect, useRef } from "react"
import { ModelMessagePart } from "@/components/builder/events/events-selected-action"
import { ChatInput } from "@/components/chat/chat-input"
import { useChat } from "@/hooks/use-chat"
import { useWorkspace } from "@/providers/workspace"

interface ChatInterfaceProps {
  chatId: string
  entityType: string
  entityId: string
}

export function ChatInterface({
  chatId,
  entityType,
  entityId,
}: ChatInterfaceProps) {
  const { workspaceId } = useWorkspace()
  const messagesEndRef = useRef<HTMLDivElement>(null)

  const {
    sendMessage,
    isSending,
    messages,
    // messagesLoading,
    // messagesError,
    isConnected,
    isThinking,
  } = useChat({ chatId, workspaceId })

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages])

  const handleSendMessage = async (message: string) => {
    try {
      await sendMessage({
        message,
        actions: [
          "core.cases.get_case",
          "core.cases.list_cases",
          "tools.slack.post_message",
          "tools.virustotal.lookup_domain",
          "tools.tavily.web_search",
        ],
        model_provider: "openai",
        instructions: `You are a helpful AI assistant helping with ${entityType} management.
        The current ${entityType} ID is ${entityId}. Be concise but thorough in your responses.`,
        context: {
          chatId,
          workspaceId,
          entityType,
          entityId,
        },
      })
    } catch (error) {
      console.error("Failed to send message:", error)
    }
  }

  // if (messagesError) {
  //   return (
  //     <div className="flex h-full items-center justify-center p-8">
  //       <div className="flex items-center gap-2 text-red-600">
  //         <AlertCircle className="h-4 w-4" />
  //         <span className="text-sm">Failed to load chat</span>
  //       </div>
  //     </div>
  //   )
  // }

  return (
    <div className="flex h-full flex-col">
      {/* Chat Header */}
      <div className="border-b bg-background px-4 py-3">
        <div className="flex items-center gap-2">
          <MessageSquare className="h-4 w-4 text-muted-foreground" />
          <h3 className="text-sm font-semibold">AI Assistant</h3>
          {isConnected && (
            <div
              className="h-2 w-2 rounded-full bg-green-500"
              title="Connected"
            />
          )}
        </div>
        <p className="text-xs text-muted-foreground mt-1">
          Conversation {chatId}
        </p>
      </div>

      {/* Messages Area */}
      <div className="flex flex-col min-w-0 gap-6 flex-1 overflow-y-scroll p-4 relative no-scrollbar">
        {messages.length === 0 && <NoMessages />}
        {messages.map((message, index) => (
          <ModelMessagePart key={index} part={message} />
        ))}
        {isThinking && (
          <div className="flex gap-3 mb-4">
            <div className="flex-shrink-0 w-8 h-8 rounded-full bg-muted flex items-center justify-center">
              <Bot className="w-4 h-4 text-muted-foreground" />
            </div>
            <div className="flex-1 max-w-xs sm:max-w-md md:max-w-lg">
              <div className="px-4 py-2 rounded-lg bg-muted">
                <div className="flex items-center gap-2">
                  <div className="flex space-x-1">
                    <div
                      className="w-2 h-2 bg-muted-foreground rounded-full animate-bounce"
                      style={{ animationDelay: "0ms" }}
                    />
                    <div
                      className="w-2 h-2 bg-muted-foreground rounded-full animate-bounce"
                      style={{ animationDelay: "150ms" }}
                    />
                    <div
                      className="w-2 h-2 bg-muted-foreground rounded-full animate-bounce"
                      style={{ animationDelay: "300ms" }}
                    />
                  </div>
                  <span className="text-sm text-muted-foreground">
                    AI is thinking...
                  </span>
                </div>
              </div>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Chat Input */}
      <ChatInput
        onSendMessage={handleSendMessage}
        disabled={isSending}
        placeholder="Ask about this case..."
      />
    </div>
  )
}

function NoMessages() {
  return (
    <div className="flex h-full items-center justify-center text-center">
      <div className="max-w-sm">
        <MessageSquare className="mx-auto h-8 w-8 text-gray-400 mb-3" />
        <h4 className="text-sm font-medium text-gray-900 mb-1">
          Start a conversation
        </h4>
        <p className="text-xs text-gray-500">
          Ask me anything about this case or get help with investigation tasks.
        </p>
      </div>
    </div>
  )
}
