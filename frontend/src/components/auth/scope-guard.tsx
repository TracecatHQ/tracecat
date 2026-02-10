"use client"

import type { ReactNode } from "react"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useScopes } from "@/providers/scopes"

interface ScopeGuardProps {
  /**
   * Single scope to check. If both `scope` and `scopes` are provided,
   * this is added to the scopes array.
   */
  scope?: string
  /**
   * Array of scopes to check against.
   */
  scopes?: string[]
  /**
   * If true, requires all scopes to be present.
   * If false or undefined, requires any one of the scopes.
   * @default false
   */
  all?: boolean
  /**
   * Content to render if the user has the required scope(s).
   */
  children: ReactNode
  /**
   * Content to render if the user doesn't have the required scope(s).
   * If not provided, nothing is rendered when access is denied.
   */
  fallback?: ReactNode
  /**
   * Content to render while loading scope information.
   * Defaults to a centered spinner.
   */
  loading?: ReactNode
}

/**
 * A component that conditionally renders its children based on user scopes.
 *
 * @example
 * // Single scope check
 * <ScopeGuard scope="workflow:create" fallback={<DisabledButton />}>
 *   <CreateWorkflowButton />
 * </ScopeGuard>
 *
 * @example
 * // Multiple scopes, any match
 * <ScopeGuard scopes={["workflow:read", "workflow:create"]} fallback={null}>
 *   <WorkflowSection />
 * </ScopeGuard>
 *
 * @example
 * // Multiple scopes, all required
 * <ScopeGuard scopes={["workflow:read", "workflow:execute"]} all fallback={<Disabled />}>
 *   <RunWorkflowButton />
 * </ScopeGuard>
 */
export function ScopeGuard({
  scope,
  scopes: scopesProp,
  all = false,
  children,
  fallback = null,
  loading,
}: ScopeGuardProps) {
  const { hasScope, hasAnyScope, hasAllScopes, isLoading } = useScopes()

  // Combine scope and scopes into a single array
  const requiredScopes: string[] = [
    ...(scope ? [scope] : []),
    ...(scopesProp ?? []),
  ]

  // If no scopes specified, render children (no restriction)
  if (requiredScopes.length === 0) {
    return <>{children}</>
  }

  // Show loading state
  if (isLoading) {
    return <>{loading ?? <CenteredSpinner />}</>
  }

  // Check scopes
  let hasAccess: boolean
  if (requiredScopes.length === 1) {
    hasAccess = hasScope(requiredScopes[0])
  } else if (all) {
    hasAccess = hasAllScopes(requiredScopes)
  } else {
    hasAccess = hasAnyScope(requiredScopes)
  }

  if (!hasAccess) {
    return <>{fallback}</>
  }

  return <>{children}</>
}

/**
 * A hook-based alternative to ScopeGuard for cases where you need
 * programmatic access to the permission check result.
 *
 * @example
 * const canCreate = useScopeCheck("workflow:create")
 * if (canCreate) {
 *   // Show create button
 * }
 */
export function useScopeCheck(
  scope?: string,
  scopes?: string[],
  options?: { all?: boolean }
): boolean | undefined {
  const { hasScope, hasAnyScope, hasAllScopes, isLoading } = useScopes()

  const requiredScopes: string[] = [
    ...(scope ? [scope] : []),
    ...(scopes ?? []),
  ]

  if (isLoading) {
    return undefined
  }

  if (requiredScopes.length === 0) {
    return true
  }

  if (requiredScopes.length === 1) {
    return hasScope(requiredScopes[0])
  }

  if (options?.all) {
    return hasAllScopes(requiredScopes)
  }

  return hasAnyScope(requiredScopes)
}
