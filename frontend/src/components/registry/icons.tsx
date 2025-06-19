import { RegistryActionRead } from "@/client"
import { LucideIcon, SquareFunctionIcon, ToyBrickIcon } from "lucide-react"

export const actionTypeToLabel: Record<
  RegistryActionRead["type"],
  { label: string; icon: LucideIcon }
> = {
  udf: {
    label: "User defined function",
    icon: SquareFunctionIcon,
  },
  template: {
    label: "Template action",
    icon: ToyBrickIcon,
  },
}
