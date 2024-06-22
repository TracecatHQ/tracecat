import { Skeleton } from "@/components/ui/skeleton"

export function ListItemSkeletion({ n = 2 }: { n: number }) {
  return (
    <>
      {[...Array(n)].map((_i, idx) => (
        <div
          key={idx}
          className="flex w-full items-center justify-center space-x-4"
        >
          <Skeleton className="size-12 rounded-full" />
          <div className="space-y-2">
            <Skeleton className="h-4 w-[250px]" />
            <Skeleton className="h-4 w-[200px]" />
          </div>
        </div>
      ))}
    </>
  )
}
