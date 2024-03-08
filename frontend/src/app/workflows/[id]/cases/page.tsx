import { promises as fs } from "fs"
import path from "path"
import { Metadata } from "next"
import { DefaultQueryClientProvider } from "@/providers/query"
import { WorkflowProvider } from "@/providers/workflow"
import { z } from "zod"

import { Separator } from "@/components/ui/separator"
import { columns } from "@/components/cases/columns"
import { DataTable } from "@/components/cases/data-table"
import { caseSchema } from "@/components/cases/data/schema"
import { Navbar } from "@/components/navbar"

export const metadata: Metadata = {
  title: "Cases | Tracecat",
}

async function getTasks() {
  const data = await fs.readFile(
    path.join(process.cwd(), "src/components/cases/data/tasks.json")
  )
  const tasks = JSON.parse(data.toString())
  return z.array(caseSchema).parse(tasks)
}

export default async function CasesPage() {
  const tasks = await getTasks()

  return (
    <>
      <DefaultQueryClientProvider>
        <WorkflowProvider>
          <div className="flex h-screen flex-col">
            <Navbar />
            <div className="flex-1 px-16 py-24">
              <DataTable data={tasks} columns={columns} />
            </div>
          </div>
        </WorkflowProvider>
      </DefaultQueryClientProvider>
    </>
  )
}
