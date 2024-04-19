from pydantic import RootModel


class ListModel[T](RootModel[list[T]]):
    def __iter__(self):
        return iter(self.root)

    def __getitem__(self, i: int):
        return self.root[i]
