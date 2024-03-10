import { z } from "zod"

import { columns } from "@/components/events/columns"
import { DataTable } from "@/components/events/data-table"
import { eventSchema } from "@/components/events/data/schema"

function getEvents() {
  const events = [
    {
      published_at: "2036-01-16T00:00:00Z",
      workflow_run_id: "5802345295",
      action_title: "Buy coffee",
      trail: { pizza_order: "Double Anchovy and Chocolate Chips" },
    },
    {
      published_at: "2036-01-16T00:00:00Z",
      workflow_run_id: "1057159188",
      action_title: "Make pizza order",
      trail: { pizza_order: "Extra Cheese and Pineapple" },
    },
  ]
  return z.array(eventSchema).parse(events)
}

export function QueryBuilder() {
  const events = getEvents()
  return (
    <div className="mb-4 flex-1">
      <DataTable data={events} columns={columns} />
    </div>
  )
}
