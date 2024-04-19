"use client"

import { useEffect, useState } from "react"
import CasesProvider, { useCasesContext } from "@/providers/cases"

import { getDistributionData } from "@/lib/utils"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import CaseTable from "@/components/cases/table"
import EventDistributionPlot, { PlotDataType } from "@/components/charts"
import { AlertNotification } from "@/components/notifications"

export default function CasesPage() {
  return (
    <CasesProvider>
      <div className="flex h-screen flex-col overflow-auto">
        <div className="flex-1 space-y-8 p-16">
          {process.env.NEXT_PUBLIC_APP_ENV === "production" && (
            <AlertNotification
              message="Cases is in preview mode, and may not work as expected"
              className="max-w-[600px]"
            />
          )}
          <CaseTable />
        </div>
      </div>
    </CasesProvider>
  )
}

function CasesStatsBanner() {
  // X axis - the category
  // Y axis - the number of elements
  const { cases } = useCasesContext()
  const [statusDistData, setStatusDistData] = useState<PlotDataType[]>([])
  const [priorityDistData, setPriorityDistData] = useState<PlotDataType[]>([])
  const [maliceDistData, setMaliceDistData] = useState<PlotDataType[]>([])
  useEffect(() => {
    const statuisDistro = getDistributionData(cases, "status")
    const priorityDistro = getDistributionData(cases, "priority")
    const maliceDistro = getDistributionData(cases, "malice")
    setStatusDistData(
      Object.entries(statuisDistro).map(([key, value]) => ({
        x: key,
        y: value,
      }))
    )
    setPriorityDistData(
      Object.entries(priorityDistro).map(([key, value]) => ({
        x: key,
        y: value,
      }))
    )
    setMaliceDistData(
      Object.entries(maliceDistro).map(([key, value]) => ({
        x: key,
        y: value,
      }))
    )
  }, [cases])
  return (
    <>
      <div className="flex-1 space-y-8 pt-6">
        <div className="grid min-h-36 gap-16 md:grid-cols-3 lg:grid-cols-3">
          <Card className="flex flex-col">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-semibold">
                Status Distribution
              </CardTitle>
            </CardHeader>
            <CardContent className="h-full">
              <EventDistributionPlot data={statusDistData} />
            </CardContent>
          </Card>
          <Card className="flex flex-col">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-semibold">
                Priority Distribution
              </CardTitle>
            </CardHeader>
            <CardContent className="h-full">
              <EventDistributionPlot data={priorityDistData} />
            </CardContent>
          </Card>
          <Card className="flex flex-col">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-semibold">
                Malice Distribution
              </CardTitle>
            </CardHeader>
            <CardContent className="h-full">
              <EventDistributionPlot data={maliceDistData} />
            </CardContent>
          </Card>
        </div>
      </div>
    </>
  )
}
