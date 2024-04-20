import { PropsWithChildren } from "react"
import { Sparkles } from "lucide-react"

import { cn } from "@/lib/utils"

interface AIGeneratedFlairProps
  extends PropsWithChildren<React.HTMLAttributes<HTMLElement>> {
  isAIGenerated: boolean
}
export function AIGeneratedFlair({
  className,
  children,
  isAIGenerated: flair,
}: AIGeneratedFlairProps) {
  return (
    <div className={cn("flex items-center", className)}>
      {flair && (
        <Sparkles className="mr-1 h-3 w-3 fill-yellow-500 text-yellow-500" />
      )}
      {children}
    </div>
  )
}
