/** Search the saved view or every authorized case in the current workspace. */
export type CaseSearchScope = "view" | "all"

/** A complete case reference that can be looked up without text matching. */
export type CaseReference =
  | { type: "short_id"; value: string }
  | { type: "uuid"; value: string }

/** Recognize complete prefixed short IDs and canonical UUIDs, not partial IDs. */
export function parseCaseReference(query: string): CaseReference | null {
  const value = query.trim()
  if (/^CASE-\d{1,10}$/i.test(value)) {
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
