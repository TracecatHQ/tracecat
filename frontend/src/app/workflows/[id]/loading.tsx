import { Skeleton } from "@/components/ui/skeleton"

export default function Loading() {
  return (
    <div className="grid h-full grid-cols-12">
      <div className="col-span-1">
        <Skeleton className="h-full w-full" />
      </div>
      <div className="col-span-8">
        <Skeleton className="h-full w-full bg-transparent" />
      </div>
      <div className="col-span-3">
        <Skeleton className="h-full w-full" />
      </div>
    </div>
  )
}
