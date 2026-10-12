import type { FormValues } from "@/lib/forms/visibility"

/**
 * Turn a workflow input name into a key that is safe as a react-hook-form
 * field name.
 *
 * react-hook-form reads `.`, `[`, `]` and quotes in a name as a path into a
 * nested object, so an input called `user.email` would be stored as
 * `{ user: { email } }` and lost. Only letters and digits pass through; `_`
 * becomes `__` and any other character becomes `_<hex code>_`, which keeps
 * distinct names distinct.
 */
export function toFieldKey(name: string): string {
  return name.replace(/[^A-Za-z0-9]/g, (char) =>
    char === "_" ? "__" : `_${char.charCodeAt(0).toString(16)}_`
  )
}

/** Recover the input name from a key made by {@link toFieldKey}. */
export function fromFieldKey(key: string): string {
  return key.replace(/__|_([0-9a-f]+)_/g, (_match, hex?: string) =>
    hex === undefined ? "_" : String.fromCharCode(Number.parseInt(hex, 16))
  )
}

/** Re-key values from input names to form state keys. */
export function toFieldKeyValues(values: FormValues): FormValues {
  return Object.fromEntries(
    Object.entries(values).map(([name, value]) => [toFieldKey(name), value])
  )
}

/** Re-key values from form state keys back to input names. */
export function fromFieldKeyValues(values: FormValues): FormValues {
  return Object.fromEntries(
    Object.entries(values).map(([key, value]) => [fromFieldKey(key), value])
  )
}
