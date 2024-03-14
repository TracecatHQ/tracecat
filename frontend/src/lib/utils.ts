import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Calculates the distribution of values in an array of objects based on a specified key.
 *
 * @template T - The type of objects in the array.
 * @param {T[]} data - The array of objects.
 * @param {string} key - The key to calculate the distribution on.
 * @returns {Object.<string, number>} - An object representing the distribution of values.
 *
 * @example
 * // Returns { "apple": 2, "banana": 3, "orange": 1 }
 * const data = [
 *   { fruit: "apple" },
 *   { fruit: "banana" },
 *   { fruit: "banana" },
 *   { fruit: "orange" },
 *   { fruit: "banana" },
 *   { fruit: "apple" }
 * ];
 * const distribution = getDistributionData(data, "fruit");
 */
export function getDistributionData<T extends Record<string, any> = any>(
  data: T[],
  key: string
): { [key: string]: number } {
  return data.reduce(
    (accumulator, currentItem) => {
      const value = (currentItem as any)[key]
      accumulator[value] = (accumulator[value] || 0) + 1
      return accumulator
    },
    {} as { [key: string]: number }
  )
}
