import { CenteredSpinner } from "@/components/loading/spinner"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

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
      <CenteredSpinner />
    </div>
  )
}
