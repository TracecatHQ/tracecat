import type { TagRead } from "@/client"
import { Badge } from "@/components/ui/badge"

export function TagBadge({ tag }: { tag: TagRead }) {
  return (
    <Badge
      key={tag.id}
      variant="secondary"
      className="text-xs"
      style={{
        backgroundColor: tag.color || undefined,
        color: tag.color ? "white" : undefined,
      }}
    >
      {tag.name}
    </Badge>
  )
}
