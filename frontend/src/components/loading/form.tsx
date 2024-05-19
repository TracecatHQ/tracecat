import { Loader2 } from "lucide-react"

import { cn } from "@/lib/utils"
import { Skeleton } from "@/components/ui/skeleton"

export function SkeletonFormLoading({
  numPanels = 10,
  className,
}: {
  numPanels?: number
  className?: string
}) {
  return (
    <div className="flex size-full flex-col items-center justify-center space-x-2 space-y-2 p-4">
      {Array.from({ length: numPanels }, (_, index) => (
        <Skeleton
          key={index}
          className={cn("flex min-h-20 w-full grow rounded-lg", className)}
        />
      ))}
    </div>
  )
}

export function FormLoading() {
  return (
    <div className="flex size-full flex-col items-center justify-center space-x-2 space-y-2 p-4">
      <Loader2 className="mx-auto animate-spin text-gray-500" />
    </div>
  )
}
