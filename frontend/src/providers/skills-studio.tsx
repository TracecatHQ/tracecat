"use client"

import { createContext, type ReactNode, useContext } from "react"
import { useSkillsStudio } from "@/components/skills/use-skills-studio"

type SkillsStudioContextValue = ReturnType<typeof useSkillsStudio>

const SkillsStudioContext = createContext<SkillsStudioContextValue | null>(null)

/**
 * Provides shared skills-studio state for the active skill so both the
 * editor page and the global controls header can render against the same
 * hook output.
 *
 * @param props.workspaceId Current workspace identifier.
 * @param props.skillId Active skill identifier.
 * @param props.children Tree consuming the studio state.
 */
export function SkillsStudioProvider({
  workspaceId,
  skillId,
  children,
}: {
  workspaceId: string
  skillId: string
  children: ReactNode
}) {
  const studio = useSkillsStudio({ workspaceId, skillId })
  return (
    <SkillsStudioContext.Provider value={studio}>
      {children}
    </SkillsStudioContext.Provider>
  )
}

/**
 * Reads the skills-studio state. Returns null when no provider is mounted
 * (e.g., on routes other than /skills/[skillId]).
 */
export function useSkillsStudioContext(): SkillsStudioContextValue | null {
  return useContext(SkillsStudioContext)
}
