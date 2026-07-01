"""IPC dispatcher: method routing, params validation, unknown-method handling."""
import pytest
from pydantic import BaseModel

from app.ipc.dispatcher import dispatch, register
from app.utils.errors import AppError


class _EchoParams(BaseModel):
    value: str


class _EchoResult(BaseModel):
    value: str


@register("test.echo", _EchoParams)
async def _handle_echo(params: _EchoParams) -> _EchoResult:
    return _EchoResult(value=params.value)


async def test_dispatch_routes_to_registered_handler():
    result = await dispatch("test.echo", {"value": "hi"})
    assert result == {"value": "hi"}


async def test_dispatch_raises_app_error_for_unknown_method():
    with pytest.raises(AppError):
        await dispatch("test.does_not_exist", {})


async def test_dispatch_raises_app_error_for_invalid_params():
    with pytest.raises(AppError):
        await dispatch("test.echo", {"wrong_field": 1})
