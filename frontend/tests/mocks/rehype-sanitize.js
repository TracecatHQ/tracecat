// Minimal rehype-sanitize mock for Jest: provides a no-op plugin and a stub schema
function rehypeSanitizeMock() {
  return function noopTransformer(tree) {
    return tree;
  };
}

const defaultSchema = { attributes: {} };

module.exports = rehypeSanitizeMock;
module.exports.default = rehypeSanitizeMock;
module.exports.defaultSchema = defaultSchema;
