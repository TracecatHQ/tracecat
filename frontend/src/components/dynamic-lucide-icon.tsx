import type { LucideProps } from "lucide-react"
import dynamicIconImports from "lucide-react/dynamicIconImports"
import { lazy, memo, type ReactNode, Suspense } from "react"

type IconName = keyof typeof dynamicIconImports

export function isValidIconName(name: string): name is IconName {
  return name in dynamicIconImports
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
  if (!isValidIconName(name)) {
    return <>{fallback}</>
  }
  const Icon = getLazyIcon(name)
  return (
    <Suspense fallback={fallback}>
      <Icon {...props} />
    </Suspense>
  )
})
