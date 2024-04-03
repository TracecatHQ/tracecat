import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

import { Action } from "@/types/schemas"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Calculates the distribution of values in an array of objects based on a specified key.
 *
 * @template T - The type of objects in the array.
 * @param {T[]} data - The array of objects.
 * @param {string} key - The key to calculate the distribution on.
 * @returns {Object.<string, number>} - An object representing the distribution of values.
 *
 * @example
 * // Returns { "apple": 2, "banana": 3, "orange": 1 }
 * const data = [
 *   { fruit: "apple" },
 *   { fruit: "banana" },
 *   { fruit: "banana" },
 *   { fruit: "orange" },
 *   { fruit: "banana" },
 *   { fruit: "apple" }
 * ];
 * const distribution = getDistributionData(data, "fruit");
 */
export function getDistributionData<T extends Record<string, any> = any>(
  data: T[],
  key: string
): { [key: string]: number } {
  return data.reduce(
    (accumulator, currentItem) => {
      const value = (currentItem as any)[key]
      accumulator[value] = (accumulator[value] || 0) + 1
      return accumulator
    },
    {} as { [key: string]: number }
  )
}
export const copyToClipboard = async ({
  target,
  message,
  value,
}: {
  target?: string
  value?: string
  message?: string
}) => {
  try {
    let copyValue = ""
    if (!navigator.clipboard) {
      throw new Error("Browser doesn't have support for native clipboard.")
    }
    if (target) {
      const node = document.querySelector(target)
      if (!node || !node.textContent) {
        throw new Error("Element not found")
      }
      value = node.textContent
    }
    if (value) {
      copyValue = value
    }
    await navigator.clipboard.writeText(copyValue)
    console.log(message ?? "Copied!!!")
  } catch (error) {
    console.log(error)
  }
}

export function slugify(value: string, delimiter: string = "_"): string {
  return value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9 ]/g, "")
    .replace(/\s+/g, delimiter)
}

export function undoSlugify(value: string, delimiter: string = "_"): string {
  return value
    .replace(new RegExp(delimiter, "g"), " ")
    .replace(/\b\w/g, (l) => l.toUpperCase())
}

/**
 *
 * @param key <Action ID>.<Action Slug>
 * @returns <Action Slug>
 */
export function getSlugFromActionKey(key: string): string {
  return key.split(".")[1]
}

export function getActionKey(action: Action): string {
  return `${action.id}.${slugify(action.title)}`
}

export function tryStringify(value: any, defaultValue: string = ""): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch (error) {
    return defaultValue
  }
}

export function parseActionRunId(
  actionRunId: string,
  field: "actionId" | "actionSlug" | "workflowRunId" = "actionSlug"
): string {
  if (!actionRunId.startsWith("ar:")) {
    throw new Error("Invalid action run ID")
  }
  const [_, actionKey, workflowRunId] = actionRunId.split(":")
  switch (field) {
    case "actionId":
      return actionKey.split(".")[0]
    case "actionSlug":
      return actionKey.split(".")[1]
    case "workflowRunId":
      return workflowRunId
    default:
      throw new Error("Invalid field")
  }
}

export const loadFromLocalStorage = <T>(key: string): T => {
  const storedValue = localStorage.getItem(key)
  return storedValue ? JSON.parse(storedValue) : []
}
export const storeInLocalStorage = <T>(key: string, value: T) => {
  localStorage.setItem(key, JSON.stringify(value))
}

export const deleteFromLocalStorage = (key: string) => {
  localStorage.removeItem(key)
}
