"use client"

import { createContext, type ReactNode, useContext, useMemo } from "react"
import type { ChatPageContext } from "@/client"

const ChatPageContextContext = createContext<ChatPageContext | null>(null)

/**
 * Tell the chats rendered below which object the page shows.
 *
 * A chat inside this provider sends the value as `page_context` with each
 * message, so a docked workspace chat knows what "this form" means. Pass no
 * `id` on a page that shows no single object, and nothing is sent.
 */
export function ChatPageContextProvider({
  kind,
  id,
  children,
}: {
  kind: ChatPageContext["kind"]
  id?: string
  children: ReactNode
}) {
  const value = useMemo(() => (id ? { kind, id } : null), [kind, id])
  return (
    <ChatPageContextContext.Provider value={value}>
      {children}
    </ChatPageContextContext.Provider>
  )
}

/** The object the surrounding page shows, or `null` on any other page. */
export function useChatPageContext(): ChatPageContext | null {
  return useContext(ChatPageContextContext)
}
