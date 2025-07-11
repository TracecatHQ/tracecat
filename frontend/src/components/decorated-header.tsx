import { cva, type VariantProps } from "class-variance-authority"
import type { LucideIcon, LucideProps } from "lucide-react"

import { cn } from "@/lib/utils"

const titleVariants = cva("flex items-center justify-between font-semibold", {
  variants: {
    titleSize: {
      xl: "text-xl",
      lg: "text-lg",
      md: "text-md",
      sm: "text-sm",
      xs: "text-xs",
    },
  },
  defaultVariants: {
    titleSize: "xl",
  },
})
const iconVariants = cva("mr-2", {
  variants: {
    iconSize: {
      xl: "h-5 w-5",
      lg: "h-4 w-4",
      md: "h-3 w-3",
      sm: "h-2 w-2",
      xs: "h-1 w-1",
    },
  },
  defaultVariants: {
    iconSize: "xl",
  },
})

export interface DecoratedHeaderProps
  extends React.HTMLAttributes<HTMLElement>,
    VariantProps<typeof titleVariants>,
    VariantProps<typeof iconVariants> {
  size?: "xl" | "lg" | "md" | "sm" | "xs"
  iconSize?: "xl" | "lg" | "md" | "sm" | "xs"
  icon?: LucideIcon
  iconProps?: LucideProps
  node: React.ReactNode
  strokeWidth?: number
}

export default function DecoratedHeader({
  className,
  size = "xl",
  icon: Icon,
  iconSize,
  node,
  iconProps,
  children,
}: DecoratedHeaderProps) {
  const titleSize = size
  const { className: iconClassName, ...otherIconProps } = iconProps ?? {}
  return (
    <div className={cn(titleVariants({ titleSize, className }))}>
      <div className="flex items-center">
        {Icon && (
          <Icon
            className={cn(iconVariants({ iconSize }), iconClassName)}
            {...otherIconProps}
          />
        )}
        {node}
      </div>
      {children}
    </div>
  )
}
