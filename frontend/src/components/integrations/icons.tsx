import { CircleCheck } from "lucide-react"
import type { IntegrationStatus } from "@/client"
import { cn } from "@/lib/utils"

export const statusStyles: Record<
  IntegrationStatus,
  {
    label: string
    style: string
  }
> = {
  connected: {
    label: "Connected",
    style: "bg-green-100 text-green-800 hover:bg-green-200",
  },
  configured: {
    label: "Configured",
    style: "bg-yellow-100 text-yellow-800 hover:bg-yellow-200",
  },
  not_configured: {
    label: "Not Configured",
    style: "bg-gray-100 text-gray-800 hover:bg-gray-200",
  },
}

export const SuccessIcon = ({ className }: { className?: string }) => {
  return (
    <CircleCheck
      className={cn(
        "size-4 border-none border-emerald-500 fill-emerald-500 stroke-white",
        className
      )}
    />
  )
}
