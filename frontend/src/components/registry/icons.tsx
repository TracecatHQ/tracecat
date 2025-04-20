import { RegistryActionRead } from "@/client"
import { LucideIcon, SquareFunctionIcon, ToyBrickIcon } from "lucide-react"

export const actionTypeToLabel: Record<
  RegistryActionRead["type"],
  { label: string; icon: LucideIcon }
> = {
  udf: {
    label: "User Defined Function",
    icon: SquareFunctionIcon,
  },
  template: {
    label: "Template Action",
    icon: ToyBrickIcon,
  },
}
