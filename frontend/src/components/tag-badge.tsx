import type { TagRead } from "@/client"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

interface TagBadgeProps {
  tag: TagRead
  className?: string
}

export function TagBadge({ tag, className }: TagBadgeProps) {
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
      className={cn(
        "border-0 text-xs leading-tight",
        tag.color && "font-medium",
        className
      )}
      style={badgeStyle}
    >
      {tag.name}
    </Badge>
  )
}
