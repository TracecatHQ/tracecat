const lucide = require("lucide-react")

function toKebabCase(name) {
  return name
    .replace(/([a-z0-9])([A-Z])/g, "$1-$2")
    .replace(/([A-Z]+)([A-Z][a-z0-9])/g, "$1-$2")
    .toLowerCase()
}

const dynamicIconImports = Object.fromEntries(
  Object.entries(lucide)
    .filter(([name, value]) => {
      if (typeof value !== "function") return false
      if (name === "createLucideIcon" || name === "Icon") return false
      return !name.endsWith("Icon")
    })
    .map(([name, value]) => [
      toKebabCase(name),
      () =>
        Promise.resolve({
          default: value,
        }),
    ])
)

module.exports = dynamicIconImports
module.exports.default = dynamicIconImports
module.exports.__esModule = true
