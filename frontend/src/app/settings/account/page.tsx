import { SignedIn, SignedOut, SignInButton, UserButton } from "@clerk/nextjs"

import { Separator } from "@/components/ui/separator"

export default function Page() {
  return (
    <div className="h-full space-y-6">
      <div className="flex items-end justify-between">
        <h3 className="text-lg font-medium">Account</h3>
      </div>
      <Separator />
      <div className="space-y-4">
        <div className="text-sm">
          <div className="flex items-center justify-between">
            <h6 className="font-bold">Clerk Settings</h6>
            <ClerkUserButton />
          </div>
          <p className="text-muted-foreground">
            Please click on the user icon on the right to access Clerk settings.
          </p>
        </div>
      </div>
    </div>
  )
}

function ClerkUserButton() {
  return (
    <>
      <SignedOut>
        <SignInButton />
      </SignedOut>
      <SignedIn>
        <UserButton />
      </SignedIn>
    </>
  )
}
