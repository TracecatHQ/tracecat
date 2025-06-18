import { IntegrationOauthCallback } from "@/client"
import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"
import YAML from "yaml"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
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
    .replace(/[\u0300-\u036f]/g, "") // Remove diacritics
    .toLowerCase()
    .trim()
    .replace(/['`]/g, "_") // Replace quotes and apostrophes with underscore
    .replace(/[^a-z0-9_ ]/g, "") // Remove other special chars except underscore
    .replace(/\s+/g, delimiter) // Replace spaces with delimiter
}

export function undoSlugify(value: string, delimiter: string = "_"): string {
  return value
    .replace(new RegExp(delimiter, "g"), " ")
    .replace(/\b\w/g, (l) => l.toUpperCase())
}

export function isServer() {
  return typeof window === "undefined"
}

export function isEmptyObjectOrNullish(value: unknown) {
  return value === null || value === undefined || isEmptyObject(value)
}
export function isEmptyObject(obj: object) {
  return typeof obj === "object" && Object.keys(obj).length === 0
}

export function itemOrEmptyString(item: unknown | undefined) {
  return isEmptyObjectOrNullish(item) ? "" : YAML.stringify(item)
}

export function capitalizeFirst(str: string) {
  return str.charAt(0).toUpperCase() + str.slice(1)
}

export function shortTimeAgo(date: Date) {
  const diffMs = Math.max(Date.now() - date.getTime(), 0)
  const diffSec = Math.floor(diffMs / 1000)

  if (diffSec < 60) return "just now"

  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) return `${diffMin}m ago`

  const diffHour = Math.floor(diffMin / 60)
  if (diffHour < 24) return `${diffHour}h ago`

  const diffDay = Math.floor(diffHour / 24)
  if (diffDay < 7) return `${diffDay}d ago`

  const diffWeek = Math.floor(diffDay / 7)
  if (diffWeek < 4) return `${diffWeek}w ago`

  const diffMonth = Math.floor(diffDay / 30)
  if (diffMonth < 12) return `${diffMonth}mo ago`

  const diffYear = Math.floor(diffDay / 365)
  return `${diffYear}y ago`
}

/**
 * Reconstructs the action type from a dunder-separated string.
 *
 * i.e. "core__transform__scatter" -> "core.transform.scatter"
 *
 * @param type The action type.
 * @returns The reconstructed action type.
 */
export function reconstructActionType(type: string) {
  return type.replaceAll("__", ".")
}

export function isIntegrationOauthCallback(
  obj: unknown
): obj is IntegrationOauthCallback {
  return (
    typeof obj === "object" &&
    obj !== null &&
    "status" in obj &&
    "provider_id" in obj &&
    "redirect_url" in obj
  )
}
