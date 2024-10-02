from typing import Callable

from ..interface import Adapter
from ..verb import Verb


class FunctionAdapter(Adapter):
    __slots__ = ("fn",)

    def __init__(self, function_pointer: Callable[[], str]):
        self.fn = function_pointer
        super().__init__()

    def __repr__(self):
        return f"<{type(self).__qualname__} fn={self.fn!r}>"

    def get_value(self, ctx: Verb) -> str:
        return str(self.fn())
