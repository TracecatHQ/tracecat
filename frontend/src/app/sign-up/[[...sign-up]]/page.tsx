import { SignUp } from "@clerk/nextjs"

export default function Page() {
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
