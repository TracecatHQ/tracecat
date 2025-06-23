import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

export function AvailabilityBadge({
  availability,
  className,
}: {
  availability: string
  className?: string
}) {
  switch (availability) {
    case "comingSoon":
      return <ComingSoonBadge className={className} />
    default:
      return null
  }
}

export function ComingSoonBadge({ className }: { className?: string }) {
  return (
    <Badge variant="outline" className={cn("bg-white py-3 text-xs", className)}>
      Coming Soon
    </Badge>
  )
}
