"use client"

import { useEffect } from "react"
import { CopilotChatInterface } from "@/components/copilot/copilot-chat-interface"

export default function CopilotPage() {
  useEffect(() => {
    if (typeof window !== "undefined") {
      document.title = "Copilot"
    }
  }, [])

  return (
    <div className="size-full">
      <CopilotChatInterface />
    </div>
  )
}
