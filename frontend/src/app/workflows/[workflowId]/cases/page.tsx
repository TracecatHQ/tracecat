"use client"

import { useEffect, useState } from "react"
import { useSession } from "@/providers/session"
import { useWorkflowMetadata } from "@/providers/workflow"
import { useQuery } from "@tanstack/react-query"

import { Case } from "@/types/schemas"
import { getCases } from "@/lib/cases"
import { getDistributionData } from "@/lib/utils"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import CaseTable from "@/components/cases/table"
import EventDistributionPlot, { PlotDataType } from "@/components/charts"

export default function CasesPage() {
  const session = useSession()

  const { workflowId } = useWorkflowMetadata()
  if (!workflowId) {
    console.error(`Non-existent workflow ${workflowId}, cannot load cases`)
    throw new Error("Non-existent workflow, cannot load cases")
  }
  const { data: cases } = useQuery<Case[], Error>({
    queryKey: ["cases"],
    queryFn: async () => {
      const cases = await getCases(session, workflowId)
      return cases
    },
  })
  return (
    <>
      <div className="flex h-screen flex-col">
        <div className="flex-1 space-y-16 px-16 py-16">
          <CasesStatsBanner cases={cases || []} />
          <CaseTable cases={cases || []} />
        </div>
      </div>
    </>
  )
}

function CasesStatsBanner({ cases }: { cases: Case[] }) {
  // X axis - the category
  // Y axis - the number of elements
  const [statusDistData, setStatusDistData] = useState<PlotDataType[]>([])
  const [priorityDistData, setPriorityDistData] = useState<PlotDataType[]>([])
  const [maliceDistData, setMaliceDistData] = useState<PlotDataType[]>([])
  useEffect(() => {
    console.log("cases", cases)
    const statuisDistro = getDistributionData(cases, "status")
    const priorityDistro = getDistributionData(cases, "priority")
    const maliceDistro = getDistributionData(cases, "malice")
    console.log("statusDistro", statuisDistro)
    console.log("priorityDistro", priorityDistro)
    console.log("maliceDistro", maliceDistro)
  }, [cases])
  return (
    <>
      <div className="flex-1 space-y-8 pt-6">
        <div className="grid min-h-36 gap-16 md:grid-cols-3 lg:grid-cols-3">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">
                Status Distribution
              </CardTitle>
            </CardHeader>
            <CardContent>
              <EventDistributionPlot data={statusDistData} />
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">
                Priority Distribution
              </CardTitle>
            </CardHeader>
            <CardContent>
              <EventDistributionPlot data={priorityDistData} />
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">
                Malice Distribution
              </CardTitle>
            </CardHeader>
            <CardContent>
              <EventDistributionPlot data={maliceDistData} />
            </CardContent>
          </Card>
        </div>
      </div>
    </>
  )
}
