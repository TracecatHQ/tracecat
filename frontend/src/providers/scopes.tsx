"use client"

import { createContext, type ReactNode, useContext, useMemo } from "react"
import { useUserScopes } from "@/lib/hooks"

interface ScopeContextValue {
  scopes: Set<string>
  isLoading: boolean
  error: unknown
  /**
   * Check if the user has a specific scope.
   * Supports wildcard matching (e.g., user has "workflow:*" and checking "workflow:read").
   */
  hasScope: (scope: string) => boolean
  /**
   * Check if the user has any of the specified scopes.
   */
  hasAnyScope: (scopes: string[]) => boolean
  /**
   * Check if the user has all of the specified scopes.
   */
  hasAllScopes: (scopes: string[]) => boolean
}

const ScopeContext = createContext<ScopeContextValue | undefined>(undefined)

/**
 * Match a required scope against a user's granted scopes.
 * Supports wildcards in granted scopes (e.g., "workflow:*" matches "workflow:read").
 */
function matchScope(
  requiredScope: string,
  grantedScopes: Set<string>
): boolean {
  // Direct match
  if (grantedScopes.has(requiredScope)) {
    return true
  }

  // Check for superuser wildcard
  if (grantedScopes.has("*")) {
    return true
  }

  // Check for pattern matches (e.g., "workflow:*" matches "workflow:read")
  for (const granted of grantedScopes) {
    const escapedGranted = granted.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")

    if (granted.endsWith(":*")) {
      const prefix = granted.slice(0, -1) // Remove the "*" to get "workflow:"
      if (requiredScope.startsWith(prefix)) {
        return true
      }
    }
    // Handle more complex wildcards like "action:tools.virustotal.*:execute"
    if (granted.includes("*")) {
      const pattern = escapedGranted.replace(/\*/g, ".*")
      const regex = new RegExp(`^${pattern}$`)
      if (regex.test(requiredScope)) {
        return true
      }
    }
  }

  return false
}

export function ScopeProvider({ children }: { children: ReactNode }) {
  const { userScopes, isLoading, error } = useUserScopes()

  const value = useMemo<ScopeContextValue>(() => {
    const scopes = new Set(userScopes?.scopes ?? [])

    const hasScope = (scope: string): boolean => {
      return matchScope(scope, scopes)
    }

    const hasAnyScope = (requiredScopes: string[]): boolean => {
      return requiredScopes.some((scope) => hasScope(scope))
    }

    const hasAllScopes = (requiredScopes: string[]): boolean => {
      return requiredScopes.every((scope) => hasScope(scope))
    }

    return {
      scopes,
      isLoading,
      error,
      hasScope,
      hasAnyScope,
      hasAllScopes,
    }
  }, [userScopes, isLoading, error])

  return <ScopeContext.Provider value={value}>{children}</ScopeContext.Provider>
}

/**
 * Hook to access the current user's scopes and scope-checking utilities.
 */
export function useScopes(): ScopeContextValue {
  const context = useContext(ScopeContext)
  if (context === undefined) {
    throw new Error("useScopes must be used within a ScopeProvider")
  }
  return context
}

/**
 * Hook to check if the user has a specific scope.
 * Returns undefined while loading.
 */
export function useHasScope(scope: string): boolean | undefined {
  const { hasScope, isLoading } = useScopes()
  if (isLoading) return undefined
  return hasScope(scope)
}

/**
 * Hook to check if the user has any of the specified scopes.
 * Returns undefined while loading.
 */
export function useHasAnyScope(scopes: string[]): boolean | undefined {
  const { hasAnyScope, isLoading } = useScopes()
  if (isLoading) return undefined
  return hasAnyScope(scopes)
}

/**
 * Hook to check if the user has all of the specified scopes.
 * Returns undefined while loading.
 */
export function useHasAllScopes(scopes: string[]): boolean | undefined {
  const { hasAllScopes, isLoading } = useScopes()
  if (isLoading) return undefined
  return hasAllScopes(scopes)
}
