import { cn } from "@/lib/utils"
import { Skeleton } from "@/components/ui/skeleton"

export function FormLoading({
  numPanels = 10,
  className,
}: {
  numPanels?: number
  className?: string
}) {
  return (
    <div className="flex h-full w-full flex-col items-center justify-center space-x-2 space-y-2 p-4">
      {Array.from({ length: numPanels }, (_, index) => (
        <Skeleton
          key={index}
          className={cn("flex min-h-20 w-full flex-grow rounded-lg", className)}
        />
      ))}
    </div>
  )
}
