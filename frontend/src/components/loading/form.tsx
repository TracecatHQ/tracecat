import { Skeleton } from "@/components/ui/skeleton"

export function FormLoading({ numPanels = 10 }: { numPanels?: number }) {
  return (
    <div className="flex h-full w-full flex-col items-center justify-center space-x-2 space-y-2 p-4">
      {Array.from({ length: numPanels }, (_, index) => (
        <Skeleton
          key={index}
          className="flex min-h-20 w-full flex-grow rounded-lg"
        />
      ))}
    </div>
  )
}
