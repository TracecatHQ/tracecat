from pydantic import RootModel


class ListModel[T](RootModel[list[T]]):
    """A generic list model that inherits from RootModel.

    Allows use of Pydantic Model methods on list[T]"""

    def __iter__(self):
        return iter(self.root)

    def __getitem__(self, i: int):
        return self.root[i]
