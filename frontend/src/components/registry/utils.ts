"use client"

import type { RegistryRepositoryReadMinimal } from "@/client"

const TRACECAT_REGISTRY_ORIGIN = "tracecat_registry"

export function isCustomRegistryOrigin(origin: string): boolean {
  return origin.startsWith("git+ssh://")
}

export function isTracecatRegistryOrigin(
  origin: string,
  platformOrigins: ReadonlySet<string>
): boolean {
  return platformOrigins.has(origin) || origin === TRACECAT_REGISTRY_ORIGIN
}

export function shortCommitSha(commitSha: string | null | undefined): string {
  return commitSha ? commitSha.slice(0, 7) : "-"
}

export function getRegistryOriginLabel(params: {
  origin: string
  platformOrigins: ReadonlySet<string>
  customOrigin?: string | null
}): string {
  const { origin, platformOrigins, customOrigin } = params

  if (isTracecatRegistryOrigin(origin, platformOrigins)) {
    return "Tracecat"
  }

  if (customOrigin && origin === customOrigin) {
    return "Custom"
  }

  return origin
}

export function getCustomRegistryRepository(
  repos: RegistryRepositoryReadMinimal[] | undefined
): RegistryRepositoryReadMinimal | null {
  return repos?.find((repo) => isCustomRegistryOrigin(repo.origin)) ?? null
}
