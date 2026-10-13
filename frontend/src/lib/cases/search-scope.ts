/** Search the saved view or every authorized case in the current workspace. */
export type CaseSearchScope = "view" | "all"

/** A syntactically complete reference; the API validates its length and range. */
export type CaseReference =
  | { type: "short_id"; value: string }
  | { type: "uuid"; value: string }

/** Recognize prefixed IDs/UUIDs; numeric text stays in ordinary search scope. */
export function parseCaseReference(query: string): CaseReference | null {
  const value = query.trim()
  if (/^CASE-\d+$/i.test(value)) {
    return { type: "short_id", value: value.toUpperCase() }
  }
  if (
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
      value
    )
  ) {
    return { type: "uuid", value: value.toLowerCase() }
  }
  return null
}
