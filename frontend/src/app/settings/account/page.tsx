"use client"

import { useAuth } from "@/providers/auth"

import { Separator } from "@/components/ui/separator"

export default function Page() {
  const { user } = useAuth()
  return (
    <div className="h-full space-y-6">
      <div className="flex items-end justify-between">
        <h3 className="text-lg font-medium">Account</h3>
      </div>
      <Separator />
      <div className="space-y-4">
        <div className="space-y-2 text-sm">
          <h6 className="font-bold">Settings</h6>
          <div className="flex items-center justify-between">
            <div className="text-muted-foreground">
              {user &&
                Object.entries(user).map(([key, value]) => (
                  <div key={key}>
                    {" "}
                    {key}: {JSON.stringify(value)}{" "}
                  </div>
                ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
