import * as React from "react"
import { Sparkles } from "lucide-react"

import { AuxClickMenuOptionProps } from "@/components/aux-click-menu"

export const tableHeaderAuxMenuConfig: AuxClickMenuOptionProps[] = [
  {
    type: "sub",
    children: (
      <div className="flex items-center">
        <span>AI Actions</span>
        <Sparkles className="ml-2 h-4 w-4" />
      </div>
    ),
    items: [
      {
        type: "item",
        children: "Autofill",
        onClick: () => {
          console.log("Autofill")
        },
      },
    ],
  },
]
