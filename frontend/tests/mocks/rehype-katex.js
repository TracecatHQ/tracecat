// Minimal noop rehype plugin mock for tests
module.exports = function rehypeKatexMock() {
  return function noopTransformer(tree) {
    return tree;
  };
};
