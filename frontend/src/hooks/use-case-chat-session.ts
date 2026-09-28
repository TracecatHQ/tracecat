"use client"

import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { useCallback } from "react"

import { useWorkspaceChatOpen } from "@/hooks/use-workspace-chat-open"

/** Read and select the case-chat session represented by the current URL. */
export function useCaseChatSession() {
  const pathname = usePathname()
  const router = useRouter()
  const searchParams = useSearchParams()
  const [, setChatOpen] = useWorkspaceChatOpen()
  const chatId = searchParams?.get("chatId") ?? undefined

  const openChatSession = useCallback(
    (sessionId: string) => {
      const nextParams = new URLSearchParams(searchParams?.toString() ?? "")
      nextParams.set("chatId", sessionId)
      router.replace(`${pathname}?${nextParams.toString()}`, { scroll: false })
      setChatOpen(true)
    },
    [pathname, router, searchParams, setChatOpen]
  )

  return { chatId, openChatSession }
}
