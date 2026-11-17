import { Badge } from "@/components/ui/badge"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"

const BADGE_CLASS_NAME = "h-5 cursor-default px-1.5 text-[10px] font-normal"

/** Compact row of abbreviated badges with the full names in tooltips. */
export function AbbreviatedBadgeList({
  items,
  abbreviate,
  max = 3,
}: {
  items: { id: string; name: string }[]
  abbreviate: (name: string) => string
  max?: number
}) {
  if (items.length === 0) {
    return <div className="text-xs text-muted-foreground">-</div>
  }
  const overflow = items.slice(max)
  return (
    <div className="flex flex-nowrap items-center gap-1">
      {items.slice(0, max).map((item) => (
        <Tooltip key={item.id}>
          <TooltipTrigger asChild>
            <Badge variant="secondary" className={BADGE_CLASS_NAME}>
              {abbreviate(item.name)}
            </Badge>
          </TooltipTrigger>
          <TooltipContent>{item.name}</TooltipContent>
        </Tooltip>
      ))}
      {overflow.length > 0 && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Badge variant="secondary" className={BADGE_CLASS_NAME}>
              +{overflow.length}
            </Badge>
          </TooltipTrigger>
          <TooltipContent>
            {overflow.map((item) => (
              <div key={item.id}>{item.name}</div>
            ))}
          </TooltipContent>
        </Tooltip>
      )}
    </div>
  )
}
