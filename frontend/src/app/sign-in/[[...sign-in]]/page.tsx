import { SignIn } from "@clerk/nextjs"

export default function Page() {
  return (
    <div className="flex h-full w-full items-center justify-center">
      <SignIn
        path="/sign-in"
        signUpUrl="/sign-up"
        forceRedirectUrl="/workflows"
      />
    </div>
  )
}
