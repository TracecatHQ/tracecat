import { Sparkles } from "lucide-react"

export function LoadingCellState() {
  return (
    <div className="duration-1500 flex animate-pulse space-x-4 fill-slate-300 text-slate-300">
      <Sparkles className="size-4 fill-slate-300 text-slate-300" />
      <div className="flex-1 space-y-6 py-1">
        <div className="h-2 rounded bg-slate-300" />
      </div>
    </div>
  )
}
