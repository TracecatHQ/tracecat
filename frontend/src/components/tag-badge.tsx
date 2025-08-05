import type { TagRead } from "@/client"
import { Badge } from "@/components/ui/badge"

function getContrastColor(hexColor: string): string {
  // Convert hex to RGB
  const hex = hexColor.replace("#", "")
  const r = parseInt(hex.substr(0, 2), 16)
  const g = parseInt(hex.substr(2, 2), 16)
  const b = parseInt(hex.substr(4, 2), 16)

  // Calculate relative luminance using WCAG formula
  const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255

  // Return black for light backgrounds, white for dark backgrounds
  return luminance > 0.5 ? "#000000" : "#ffffff"
}

export function TagBadge({ tag }: { tag: TagRead }) {
  return (
    <Badge
      key={tag.id}
      variant="secondary"
      className="text-xs"
      style={{
        backgroundColor: tag.color || undefined,
        color: tag.color ? getContrastColor(tag.color) : undefined,
      }}
    >
      {tag.name}
    </Badge>
  )
}
