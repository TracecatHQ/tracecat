"use client"

import React from "react"
import type { EChartsOption } from "echarts"
import ReactECharts from "echarts-for-react"

export type PlotDataType = {
  x: string
  y: number
}
export function EventDistributionPlot({ data }: { data: PlotDataType[] }) {
  if (!data || data.length === 0) {
    return <NoDataView />
  }
  const options: EChartsOption = {
    grid: { top: 8, right: 8, bottom: 24, left: 36 },
    xAxis: {
      type: "category",
      data: data.map((d) => d.x),
    },
    yAxis: {
      type: "value",
    },
    series: [
      {
        data: data.map((d) => d.y),
        type: "bar",
      },
    ],
    tooltip: {
      trigger: "axis",
    },
  }

  return (
    <ReactECharts
      option={options}
      style={{
        height: "100%",
        width: "100%",
      }}
    />
  )
}
function NoDataView() {
  return (
    <span className="flex h-full items-center justify-center text-xs text-muted-foreground">
      No data available.
    </span>
  )
}
export default EventDistributionPlot
