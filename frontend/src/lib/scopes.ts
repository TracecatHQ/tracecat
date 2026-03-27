"use client"

export function hasGrantedScope(
  requiredScope: string,
  grantedScopes: Iterable<string>
): boolean {
  const scopes =
    grantedScopes instanceof Set ? grantedScopes : new Set(grantedScopes)

  if (scopes.has(requiredScope)) {
    return true
  }

  if (scopes.has("*")) {
    return true
  }

  for (const granted of scopes) {
    if (granted.endsWith(":*")) {
      const prefix = granted.slice(0, -1)
      if (requiredScope.startsWith(prefix)) {
        return true
      }
    }

    if (granted.includes("*")) {
      const pattern = granted
        .replace(/[.+?^${}()|[\]\\]/g, "\\$&")
        .replace(/\*/g, ".*")
      const regex = new RegExp(`^${pattern}$`)
      if (regex.test(requiredScope)) {
        return true
      }
    }
  }

  return false
}
