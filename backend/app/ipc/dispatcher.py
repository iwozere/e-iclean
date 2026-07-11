"""Method registry: maps "noun.verb" IPC methods to typed handler functions.

Handlers are registered by app.ipc.handlers (the wiring layer — see AGENTS.md §9,
"routers thin, services do the work"). This module stays generic.
"""
from typing import Any, Awaitable, Callable, Type, TypeVar

from pydantic import BaseModel

from app.utils.errors import BACKEND_INTERNAL, app_error

Handler = Callable[[Any], Awaitable[BaseModel]]
P = TypeVar("P", bound=BaseModel)
R = TypeVar("R", bound=BaseModel)


class _MethodSpec:
    def __init__(self, params_model: Type[BaseModel], handler: Handler) -> None:
        self.params_model = params_model
        self.handler = handler


_registry: dict[str, _MethodSpec] = {}


def register(method: str, params_model: Type[P]):
    """Decorator: register `handler` as the implementation of IPC method `method`."""

    def decorator(handler: Callable[[P], Awaitable[R]]) -> Callable[[P], Awaitable[R]]:
        _registry[method] = _MethodSpec(params_model, handler)
        return handler

    return decorator


async def dispatch(method: str, raw_params: dict[str, Any]) -> dict[str, Any]:
    """
    Validate params and invoke the registered handler for `method`.

    Args:
        method: The IPC method name, e.g. "transfer.start".
        raw_params: The raw `params` object from the request.

    Returns:
        The handler's result, serialized to a plain dict.

    Raises:
        AppError: if the method is unknown or params fail validation.
    """
    spec = _registry.get(method)
    if spec is None:
        raise app_error(BACKEND_INTERNAL, detail=f"Unknown method: {method}")

    try:
        parsed_params = spec.params_model.model_validate(raw_params or {})
    except Exception as exc:  # pylint: disable=broad-except
        raise app_error(BACKEND_INTERNAL, detail=f"Invalid params for {method}: {exc}") from exc

    result = await spec.handler(parsed_params)
    return result.model_dump(mode="json")


def registered_methods() -> list[str]:
    """Return the list of currently registered method names (used by tests)."""
    return sorted(_registry)


def clear_registry() -> None:
    """Test helper: reset the registry between test modules if needed."""
    _registry.clear()
