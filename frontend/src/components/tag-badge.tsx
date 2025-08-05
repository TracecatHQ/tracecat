import type { TagRead } from "@/client"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

export function TagBadge({ tag }: { tag: TagRead }) {
  // Use Tailwind's contrast utilities via CSS custom properties
  const badgeStyle = tag.color
    ? {
        backgroundColor: tag.color,
        "--tw-text-opacity": "1",
        color: "rgb(255 255 255 / var(--tw-text-opacity))",
        textShadow: "0 0 2px rgba(0,0,0,0.5)",
      }
    : undefined

  return (
    <Badge
      key={tag.id}
      variant="secondary"
      className={cn("text-xs", tag.color && "font-medium")}
      style={badgeStyle}
    >
      {tag.name}
    </Badge>
  )
}
