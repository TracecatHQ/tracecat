import { cva, type VariantProps } from "class-variance-authority"
import { Eye, EyeOff } from "lucide-react"
import * as React from "react"

import { cn } from "@/lib/utils"

export const inputVariants = cva(
  "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-xs transition-colors file:border-0 file:bg-transparent file:text-xs file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
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

const PasswordInput = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, variant, ...props }, forwardedRef) => {
    const inputRef = React.useRef<HTMLInputElement | null>(null)
    const [isRevealed, setIsRevealed] = React.useState(false)

    React.useImperativeHandle(
      forwardedRef,
      () => inputRef.current as HTMLInputElement
    )

    return (
      <div className={cn("relative", className)}>
        <input
          ref={inputRef}
          type={isRevealed ? "text" : "password"}
          className={cn(inputVariants({ variant }), "pr-10")}
          {...props}
        />
        <button
          type="button"
          className="absolute inset-y-0 right-0 flex items-center rounded-md pr-3 text-muted-foreground transition hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          onClick={() => setIsRevealed((prev) => !prev)}
        >
          <span className="sr-only">
            {isRevealed ? "Hide password" : "Show password"}
          </span>
          {isRevealed ? (
            <EyeOff aria-hidden="true" className="size-4" />
          ) : (
            <Eye aria-hidden="true" className="size-4" />
          )}
        </button>
      </div>
    )
  }
)
PasswordInput.displayName = "PasswordInput"

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type = "text", variant, ...props }, forwardedRef) => {
    if (type === "password") {
      return (
        <PasswordInput
          {...props}
          className={className}
          variant={variant}
          ref={forwardedRef}
        />
      )
    }

    return (
      <input
        ref={forwardedRef}
        type={type}
        className={cn(inputVariants({ variant }), className)}
        {...props}
      />
    )
  }
)
Input.displayName = "Input"

export { Input }
