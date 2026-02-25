export type RegistryLockOrigins = Record<string, string>

function isStringRecord(value: unknown): value is Record<string, string> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false
  }
  return Object.entries(value).every(
    ([key, item]) => typeof key === "string" && typeof item === "string"
  )
}

export function getRegistryLockOrigins(
  registryLock: unknown
): RegistryLockOrigins | null {
  if (
    registryLock === null ||
    typeof registryLock !== "object" ||
    Array.isArray(registryLock)
  ) {
    return null
  }

  const candidate = registryLock as Record<string, unknown>
  const origins = candidate["origins"]
  if (isStringRecord(origins)) {
    return origins
  }

  // Legacy shape where registry_lock itself was {origin: version}.
  if (isStringRecord(registryLock)) {
    return registryLock
  }

  return null
}

export function sortRegistryLockOrigins(
  registryLock: unknown
): Array<[string, string]> {
  const origins = getRegistryLockOrigins(registryLock)
  if (!origins) {
    return []
  }
  return Object.entries(origins).sort(([left], [right]) =>
    left.localeCompare(right)
  )
}
