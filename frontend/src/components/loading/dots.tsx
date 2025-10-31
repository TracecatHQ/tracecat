"use client"

import { motion } from "motion/react"

export function Dots() {
  const dotVariants = {
    initial: { scale: 1 },
    animate: { scale: [1, 1.5, 1] },
  }

  return (
    <div className="flex space-x-1 items-center" data-testid="dots-loader">
      {[0, 1, 2].map((index) => (
        <motion.div
          key={index}
          className="size-[3px] bg-gray-500 rounded-full"
          variants={dotVariants}
          initial="initial"
          animate="animate"
          transition={{
            duration: 0.6,
            repeat: Number.POSITIVE_INFINITY,
            delay: index * 0.2,
            ease: "easeInOut",
          }}
        />
      ))}
    </div>
  )
}
