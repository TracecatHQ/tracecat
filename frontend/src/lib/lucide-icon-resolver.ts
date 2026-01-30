import type { LucideIcon } from "lucide-react"
import * as LucideIcons from "lucide-react"

/**
 * Resolve a Lucide icon name (kebab-case, e.g. "shield-check") to the React component.
 * Returns undefined when the name is null/undefined or the icon is not found.
 */
export function resolveLucideIcon(
  name?: string | null
): LucideIcon | undefined {
  if (!name) return undefined
  // Convert kebab-case to PascalCase, e.g. "shield-check" -> "ShieldCheck"
  const pascalName = name
    .split("-")
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join("")
  // lucide-react exports icons in PascalCase (no "Icon" suffix in newer versions)
  const icon =
    (LucideIcons as Record<string, unknown>)[pascalName] ??
    (LucideIcons as Record<string, unknown>)[`${pascalName}Icon`]
  return icon as LucideIcon | undefined
}
