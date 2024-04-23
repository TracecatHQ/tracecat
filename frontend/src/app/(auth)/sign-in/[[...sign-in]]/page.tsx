import { SignIn } from "@clerk/nextjs"

export default function Page() {
  return (
    <div className="flex justify-center">
      <SignIn path="/sign-in" signUpUrl="/sign-up" />
    </div>
  )
}
