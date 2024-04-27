import { SignIn } from "@clerk/nextjs"

import { authConfig } from "@/config/auth"
import { AuthDisabled } from "@/components/auth-disabled"

export default function Page() {
  if (authConfig.disabled) {
    return <AuthDisabled />
  }
  return (
    <div className="flex h-full w-full items-center justify-center">
      <SignIn
        // NOTE: Don't force redirect here as when a session expires, the user
        // should be redirected to the page they were on before relogging.
        path="/sign-in"
        signUpUrl="/sign-up"
        appearance={{
          elements: {
            logoBox: "w-full flex size-16 justify-center",
          },
        }}
      />
    </div>
  )
}
