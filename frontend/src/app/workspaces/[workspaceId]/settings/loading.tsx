import { Loader2 } from "lucide-react"

export default async function Loading() {
  return (
    <div className="flex size-full flex-col items-center justify-center">
      <Loader2 className="mx-auto animate-spin text-gray-500" />
    </div>
  )
}
