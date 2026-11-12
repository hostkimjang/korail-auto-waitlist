"""Exercise the production click boundary in an isolated browser, without rail requests."""

from __future__ import annotations

from urllib.parse import quote

import pytest
import pytest_asyncio

from rail_waitlist.korail_sidecar.pydoll.chromium_lifecycle import (
    _load_pydoll_runtime,
    configure_chromium_options,
)
from rail_waitlist.korail_sidecar.pydoll.reservation_driver import (
    _SEAT_CLICK_PREPARE_SCRIPT,
    PydollReservationDomDriver,
)


@pytest_asyncio.fixture
async def click_tab():
    runtime = _load_pydoll_runtime()
    options = runtime.options_factory()
    configure_chromium_options(options, headless=True, allow_dedicated_profile=False)
    for argument in (
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-sync",
    ):
        if argument not in options.arguments:
            options.add_argument(argument)
    async with runtime.browser_factory(options=options) as browser:
        yield await browser.start()


async def load_fixture(tab, tag: str = "button", *, late_cover: bool = False):
    markup = f"""<!doctype html><meta charset=utf-8>
      <{tag} class=reservbtn style="position:fixed;left:30px;top:30px;width:120px;height:50px"
        onclick="window.deliveries++;window.trusted=event.isTrusted">예매</{tag}>
      <script>window.deliveries=0;
        document.querySelector('.reservbtn').onmousedown=()=>{{
          if ({str(late_cover).lower()} && !window.coverCreated) {{
            window.coverCreated=true;
            const cover=document.createElement('div');cover.id='cover';
            cover.style='position:fixed;inset:0;z-index:100;background:white';
            document.body.append(cover);
          }}
        }};
      </script>"""
    await tab.go_to("data:text/html;charset=utf-8," + quote(markup))
    return await tab.query(".reservbtn")


async def value(tab, expression: str):
    response = await tab.execute_script(expression, return_by_value=True)
    return response["result"]["result"]["value"]


@pytest.mark.parametrize("tag", ["button", "a"])
async def test_native_input_reaches_exact_control_once_as_trusted_event(click_tab, tag):
    control = await load_fixture(click_tab, tag)
    assert await PydollReservationDomDriver._dispatch_reservation_control(
        control,
        prepare_script=_SEAT_CLICK_PREPARE_SCRIPT,
    )
    assert await value(click_tab, "window.deliveries") == 1
    assert await value(click_tab, "window.trusted") is True


@pytest.mark.parametrize("tag", ["button", "a"])
async def test_late_overlay_is_proven_undelivered_before_single_successful_input(click_tab, tag):
    control = await load_fixture(click_tab, tag, late_cover=True)
    assert not await PydollReservationDomDriver._dispatch_reservation_control(
        control,
        prepare_script=_SEAT_CLICK_PREPARE_SCRIPT,
    )
    assert await value(click_tab, "window.deliveries") == 0
    await click_tab.execute_script("document.querySelector('#cover').remove()")
    assert await PydollReservationDomDriver._dispatch_reservation_control(
        control,
        prepare_script=_SEAT_CLICK_PREPARE_SCRIPT,
    )
    assert await value(click_tab, "window.deliveries") == 1
    assert await value(click_tab, "window.trusted") is True


async def test_javascript_click_is_distinct_from_native_input(click_tab):
    control = await load_fixture(click_tab)
    await control.execute_script("this.click()")
    assert await value(click_tab, "window.deliveries") == 1
    assert await value(click_tab, "window.trusted") is False
