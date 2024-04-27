import { SignUp } from "@clerk/nextjs"

import { authConfig } from "@/config/auth"
import { AuthDisabled } from "@/components/auth-disabled"

export default function Page() {
  if (authConfig.disabled) {
    return <AuthDisabled />
  }
  return (
    <div className="flex h-full w-full items-center justify-center">
      <SignUp
        path="/sign-up"
        signInUrl="/sign-in"
        forceRedirectUrl="/workflows"
        appearance={{
          elements: {
            logoBox: "w-full flex size-16 justify-center",
          },
        }}
      />
    </div>
  )
}
