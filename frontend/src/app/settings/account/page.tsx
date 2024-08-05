import { authConfig } from "@/config/auth"
import { Separator } from "@/components/ui/separator"

export default function Page() {
  return (
    <div className="h-full space-y-6">
      <div className="flex items-end justify-between">
        <h3 className="text-lg font-medium">Account</h3>
      </div>
      <Separator />
      <div className="space-y-4">
        <div className="space-y-2 text-sm">
          <h6 className="font-bold">Settings</h6>
          {authConfig.disabled ? (
            <div className="text-sm text-muted-foreground">
              Authentication is disabled.
            </div>
          ) : (
            <div className="flex items-center justify-between">
              <p className="text-muted-foreground">
                Please click on the user icon on the right to access Clerk
                settings. t{" "}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
