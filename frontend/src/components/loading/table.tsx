import { Sparkles } from "lucide-react"

export function LoadingCellState() {
  return (
    <div className="duration-1500 flex animate-pulse space-x-4 fill-muted-foreground/50 text-muted-foreground/50">
      <Sparkles className="size-4 fill-muted-foreground/50 text-muted-foreground/50" />
      <div className="flex-1 space-y-6 py-1">
        <div className="h-2 rounded bg-muted" />
      </div>
    </div>
  )
}
