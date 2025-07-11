import { Sparkles } from "lucide-react"
import type { PropsWithChildren } from "react"

import { cn } from "@/lib/utils"

interface AIGeneratedFlairProps
  extends PropsWithChildren<React.HTMLAttributes<HTMLElement>> {
  isAIGenerated?: boolean
}
export function AIGeneratedFlair({
  className,
  children,
  isAIGenerated: flair = false,
}: AIGeneratedFlairProps) {
  return (
    <div className={cn("flex items-center", className)}>
      {flair && (
        <Sparkles className="mr-1 size-3 fill-yellow-500/70 text-amber-500/70" />
      )}
      {children}
    </div>
  )
}
