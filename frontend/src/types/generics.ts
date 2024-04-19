/**
 * Example usage:
 *
 * export type MyCustomPair = NamedPair<"id", "value", number>
 *
 * const myCustomPair: MyCustomPair = {
 *  id: 1,
 * value: 2
 * }
 *
 */
export type NamedPair<
  KeyName extends string,
  ValueName extends string,
  TValue = string,
> = Record<KeyName | ValueName, TValue>
