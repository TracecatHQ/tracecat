"use client"

import { createContext, useContext } from "react"
import { useLocalStorage } from "@/hooks/use-local-storage"

/** How much of an agent's work a chat draws. Display only. */
export type ChatVerbosity = "minimal" | "default" | "verbose"

const DEFAULT_CHAT_VERBOSITY: ChatVerbosity = "default"

const CHAT_VERBOSITY_LEVELS: readonly ChatVerbosity[] = [
  "minimal",
  "default",
  "verbose",
]

/** What each verbosity level draws. */
export type ChatVerbosityPolicy = {
  /** Steps start open instead of folded to one line. */
  expandSteps: boolean
  /** A finished turn folds its steps into one "Worked for" line. */
  foldFinishedTurns: boolean
  /** Harness plumbing such as tool search draws as a step. */
  showInternalTools: boolean
  /**
   * Every step draws. Otherwise only failed steps do, and a running turn
   * draws one working line in their place.
   */
  showSteps: boolean
  /** A turn's cited sources draw above it. */
  showSources: boolean
}

const CHAT_VERBOSITY_POLICIES: Record<ChatVerbosity, ChatVerbosityPolicy> = {
  minimal: {
    expandSteps: false,
    foldFinishedTurns: false,
    showInternalTools: false,
    showSteps: false,
    showSources: false,
  },
  default: {
    expandSteps: false,
    foldFinishedTurns: true,
    showInternalTools: false,
    showSteps: true,
    showSources: true,
  },
  verbose: {
    expandSteps: true,
    foldFinishedTurns: false,
    showInternalTools: true,
    showSteps: true,
    showSources: true,
  },
}

/** The display rules for a verbosity level. */
export function getChatVerbosityPolicy(
  verbosity: ChatVerbosity
): ChatVerbosityPolicy {
  return CHAT_VERBOSITY_POLICIES[verbosity]
}

/** The verbosity a chat surface draws its messages at. */
export const ChatVerbosityContext = createContext<ChatVerbosity>(
  DEFAULT_CHAT_VERBOSITY
)

/** The display rules of the surrounding chat surface. */
export function useChatVerbosityPolicy(): ChatVerbosityPolicy {
  return getChatVerbosityPolicy(useContext(ChatVerbosityContext))
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
