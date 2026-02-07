import type { LucideProps } from "lucide-react"
import dynamicIconImports from "lucide-react/dynamicIconImports"
import { lazy, memo, type ReactNode, Suspense } from "react"

type IconName = keyof typeof dynamicIconImports

function buildLegacyIconCandidates(rawName: string): string[] {
  const normalized = rawName.trim().toLowerCase().replace(/_/g, "-")
  if (!normalized) return []

  const strippedPrefix = normalized.replace(/^lucide-/, "")
  const strippedSuffix = strippedPrefix.replace(/-icon$/, "")
  const withDigitBreaks = strippedSuffix.replace(/([a-z])(\d)/g, "$1-$2")

  const candidates = [
    normalized,
    strippedPrefix,
    strippedSuffix,
    withDigitBreaks,
  ]
  return Array.from(new Set(candidates.filter(Boolean)))
}

export function resolveIconName(name: string): IconName | null {
  for (const candidate of buildLegacyIconCandidates(name)) {
    if (candidate in dynamicIconImports) {
      return candidate as IconName
    }
  }
  return null
}

export function isValidIconName(name: string): boolean {
  return resolveIconName(name) !== null
}

const cache = new Map<
  IconName,
  React.LazyExoticComponent<React.ComponentType<Omit<LucideProps, "ref">>>
>()

function getLazyIcon(name: IconName) {
  let component = cache.get(name)
  if (!component) {
    component = lazy(dynamicIconImports[name])
    cache.set(name, component)
  }
  return component
}

interface DynamicLucideIconProps extends Omit<LucideProps, "ref"> {
  /** Kebab-case icon name, e.g. "shield-check" */
  name: string
  /** Fallback to render while loading or if the icon name is invalid */
  fallback?: ReactNode
}

export const DynamicLucideIcon = memo(function DynamicLucideIcon({
  name,
  fallback = null,
  ...props
}: DynamicLucideIconProps) {
  const resolvedName = resolveIconName(name)
  if (!resolvedName) {
    return <>{fallback}</>
  }
  const Icon = getLazyIcon(resolvedName)
  return (
    <Suspense fallback={fallback}>
      <Icon {...props} />
    </Suspense>
  )
})
