import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

export const inputVariants = cva(
  "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "",
        flat: "rounded border-transparent bg-transparent shadow-none focus:cursor-text hover:rounded-md hover:bg-muted-foreground/10 hover:cursor-pointer focus:rounded-md focus:border-input focus:bg-background focus:shadow-sm focus:hover:border-input focus:hover:bg-background focus:ring-0 focus-visible:ring-0",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

export type InputVariant = VariantProps<typeof inputVariants>["variant"]

export interface InputProps
  extends React.InputHTMLAttributes<HTMLInputElement>,
    VariantProps<typeof inputVariants> {}

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, variant, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(inputVariants({ variant }), className)}
        ref={ref}
        {...props}
      />
    )
  }
)
Input.displayName = "Input"

export { Input }
