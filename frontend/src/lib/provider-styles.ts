import type { ProviderCategory } from "@/client"

/**
 * Shared category colors for provider badges across the application
 */
export const categoryColors: Record<ProviderCategory, string> = {
  auth: "bg-green-100 text-green-800 hover:bg-green-200",
  communication: "bg-pink-100 text-pink-800 hover:bg-pink-200",
  cloud: "bg-blue-100 text-blue-800 hover:bg-blue-200",
  monitoring: "bg-orange-100 text-orange-800 hover:bg-orange-200",
  alerting: "bg-red-100 text-red-800 hover:bg-red-200",
  other: "bg-gray-100 text-gray-800 hover:bg-gray-200",
}
