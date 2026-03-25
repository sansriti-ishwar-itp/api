from typing import Any

from pydantic import RootModel


class CreateServerRequest(RootModel[dict[str, Any]]):
    """
    Passthrough JSON body for `openstacksdk`'s `conn.compute.create_server(**attrs)`.
    """

