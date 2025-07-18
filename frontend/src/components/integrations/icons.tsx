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
    style: "bg-green-100 text-green-800 border-transparent",
  },
  configured: {
    label: "Connection incomplete",
    style: "bg-yellow-100 text-yellow-800 border-transparent",
  },
  not_configured: {
    label: "Not configured",
    style: "bg-gray-100 text-gray-800 border-transparent",
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
