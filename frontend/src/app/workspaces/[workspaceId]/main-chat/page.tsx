"use client"

import { useEffect } from "react"
import { MainChatInterface } from "@/components/main-chat/main-chat-interface"

export default function MainChatPage() {
  useEffect(() => {
    if (typeof window !== "undefined") {
      document.title = "Chat"
    }
  }, [])

  return (
    <div className="size-full">
      <MainChatInterface />
    </div>
  )
}
