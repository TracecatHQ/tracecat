"use client"

import { createContext, type ReactNode, useContext, useMemo } from "react"
import { useUserScopes } from "@/lib/hooks"
import { hasGrantedScope } from "@/lib/scopes"
import { useOptionalWorkspaceId } from "@/providers/workspace-id"

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

export function ScopeProvider({ children }: { children: ReactNode }) {
  const workspaceId = useOptionalWorkspaceId()
  const { userScopes, isLoading, error } = useUserScopes(workspaceId)

  const value = useMemo<ScopeContextValue>(() => {
    const scopes = new Set(userScopes?.scopes ?? [])

    const hasScope = (scope: string): boolean => {
      return hasGrantedScope(scope, scopes)
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
