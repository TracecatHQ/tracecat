import { z } from "zod"

const emailSchema = z.string().email()

/**
 * Parse a free-form string of emails separated by commas, spaces, semicolons,
 * or newlines into deduplicated lists of valid and invalid addresses.
 *
 * Emails are trimmed and lowercased. Duplicates (case-insensitive) are
 * collapsed. Invalid tokens are returned separately so the caller can surface
 * them to the user.
 *
 * @param raw - The raw textarea contents.
 * @returns An object with `valid` and `invalid` email arrays.
 */
export function parseEmailList(raw: string): {
  valid: string[]
  invalid: string[]
} {
  const tokens = raw
    .split(/[\s,;]+/)
    .map((t) => t.trim().toLowerCase())
    .filter((t) => t.length > 0)

  const seen = new Set<string>()
  const valid: string[] = []
  const invalid: string[] = []

  for (const token of tokens) {
    if (seen.has(token)) {
      continue
    }
    seen.add(token)
    if (emailSchema.safeParse(token).success) {
      valid.push(token)
    } else {
      invalid.push(token)
    }
  }

  return { valid, invalid }
}
