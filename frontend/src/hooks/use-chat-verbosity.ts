"use client"

import { createContext, useContext } from "react"
import { useLocalStorage } from "@/hooks/use-local-storage"

/** How much of an agent's work a chat draws. Display only. */
export type ChatVerbosity = "minimal" | "default" | "verbose"

export const DEFAULT_CHAT_VERBOSITY: ChatVerbosity = "default"

const CHAT_VERBOSITY_LEVELS: readonly ChatVerbosity[] = [
  "minimal",
  "default",
  "verbose",
]

/** The verbosity a chat surface draws its messages at. */
export const ChatVerbosityContext = createContext<ChatVerbosity>(
  DEFAULT_CHAT_VERBOSITY
)

/** The verbosity of the surrounding chat surface. */
export function useChatVerbosity(): ChatVerbosity {
  return useContext(ChatVerbosityContext)
}

/** This browser's agent verbosity preference, and its setter. */
export function useChatVerbosityPreference(): [
  ChatVerbosity,
  (verbosity: ChatVerbosity) => void,
] {
  const [stored, setStored] = useLocalStorage<string>(
    "agent-verbosity",
    DEFAULT_CHAT_VERBOSITY,
    "tracecat"
  )
  // A stale or hand-edited value falls back to the default.
  const verbosity = CHAT_VERBOSITY_LEVELS.find((level) => level === stored)
  return [verbosity ?? DEFAULT_CHAT_VERBOSITY, setStored]
}
