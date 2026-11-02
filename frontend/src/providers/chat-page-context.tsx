"use client"

import { createContext } from "react"
import type { ChatPageContext } from "@/client"

/**
 * The object the surrounding page shows, or `null`. A chat rendered below a
 * provider sends it as `page_context` with each message, so a docked
 * workspace chat knows what "this form" means.
 */
export const PageContext = createContext<ChatPageContext | null>(null)
