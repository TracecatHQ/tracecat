import { promises as fs } from "fs"
import path from "path"
import { Metadata } from "next"
import { DefaultQueryClientProvider } from "@/providers/query"
import { SelectedWorkflowProvider } from "@/providers/selected-workflow"
import { z } from "zod"

import { columns } from "@/components/cases/columns"
import { DataTable } from "@/components/cases/data-table"
import { taskSchema } from "@/components/cases/data/schema"
import { Navbar } from "@/components/navbar"

export const metadata: Metadata = {
  title: "Cases | Tracecat",
}

async function getTasks() {
  const data = await fs.readFile(
    path.join(process.cwd(), "src/components/cases/data/tasks.json")
  )
  const tasks = JSON.parse(data.toString())
  return z.array(taskSchema).parse(tasks)
}

export default async function CasesPage() {
  const tasks = await getTasks()

  return (
    <>
      <DefaultQueryClientProvider>
        <SelectedWorkflowProvider>
          <div className="flex h-screen flex-col">
            <Navbar />
            <div className="w-full flex-1 space-y-8 p-8">
              <DataTable data={tasks} columns={columns} />
            </div>
          </div>
        </SelectedWorkflowProvider>
      </DefaultQueryClientProvider>
    </>
  )
}
