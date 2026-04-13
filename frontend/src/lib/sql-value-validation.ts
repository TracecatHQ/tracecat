const SQL_INTEGER_INPUT_PATTERN = /^[+-]?\d+$/
const SQL_NUMERIC_INPUT_PATTERN =
  /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/

/**
 * Check whether user input matches the backend's accepted INTEGER syntax.
 */
export function isValidSqlIntegerInput(value: string): boolean {
  const trimmed = value.trim()
  return trimmed.length > 0 && SQL_INTEGER_INPUT_PATTERN.test(trimmed)
}

/**
 * Check whether user input matches the backend's accepted NUMERIC syntax.
 */
export function isValidSqlNumericInput(value: string): boolean {
  const trimmed = value.trim()
  return trimmed.length > 0 && SQL_NUMERIC_INPUT_PATTERN.test(trimmed)
}
