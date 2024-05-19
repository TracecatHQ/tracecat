import { Loader2 } from "lucide-react"

import { cn } from "@/lib/utils"

export default function Loading() {
  return (
    <div className="flex size-full items-center justify-center">
      <Loader2 className={cn("h-6 w-6 animate-spin text-muted-foreground")} />
    </div>
  )
}
