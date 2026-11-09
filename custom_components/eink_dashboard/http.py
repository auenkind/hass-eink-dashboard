# Copyright 2026 Andreas Schneider
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Authenticated layout API and public PNG/BMP image HTTP views."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

from .battery import build_entity_state, resolve_battery_level
from .bmp import BmpDepth
from .const import (
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    MAX_WIDGETS,
    WidgetType,
    default_list_row_height,
    resolve_device_preset,
)
from .data import get_loaded_entry

if TYPE_CHECKING:
    from .data import EinkDashboardConfigEntry

_LOGGER = logging.getLogger(__name__)


class EinkLayoutView(HomeAssistantView):
    """Authenticated API view to read and write the widget layout."""

    url = "/api/eink_dashboard/{entry_id}/layout"
    name = "api:eink_dashboard:layout"
    requires_auth = True

    async def get(self, request: web.Request, entry_id: str) -> web.Response:
        """Return the current widget list and display config as JSON."""
        hass = request.app["hass"]
        entry = get_loaded_entry(hass, entry_id)
        if entry is None:
            raise web.HTTPNotFound()

        widgets = entry.runtime_data.widgets
        width = entry.options.get("width", DEFAULT_WIDTH)
        height = entry.options.get("height", DEFAULT_HEIGHT)

        device_model = entry.options.get("device_model", "custom")
        preset = resolve_device_preset(entry.options)

        battery_entity_id = entry.options.get("battery_entity_id")
        states: dict = {}
        if battery_entity_id:
            entity_state = build_entity_state(hass, battery_entity_id)
            if entity_state is not None:
                states[battery_entity_id] = entity_state
        device_battery_level, _ = resolve_battery_level(
            battery_entity_id,
            states,
            entry.runtime_data.battery_sensor,
        )

        return web.json_response(
            {
                "widgets": widgets,
                "display": {
                    "width": width,
                    "height": height,
                    "display_levels": preset.display_levels,
                    "row_height": default_list_row_height(entry.options),
                },
                "device": {
                    "name": entry.title,
                    "model": device_model,
                    "model_label": preset.label,
                    "orientation": entry.options.get(
                        "orientation", "portrait"
                    ),
                    "area_id": entry.options.get("area_id"),
                    "has_webhooks": bool(
                        entry.options.get("webhook_urls", [])
                    ),
                    "device_battery_level": device_battery_level,
                },
            }
        )

    async def post(self, request: web.Request, entry_id: str) -> web.Response:
        """Validate and persist a new widget list, then trigger a refresh."""
        hass = request.app["hass"]
        entry = get_loaded_entry(hass, entry_id)
        if entry is None:
            raise web.HTTPNotFound()

        try:
            body = await request.json()
        except ValueError:
            raise web.HTTPBadRequest(text="Invalid JSON") from None

        if not isinstance(body, list):
            raise web.HTTPBadRequest(text="Body must be a list")

        if len(body) > MAX_WIDGETS:
            raise web.HTTPBadRequest(text="Too many widgets")

        valid_types = {t.value for t in WidgetType}
        # Field values may be any JSON type, including nested objects
        # (e.g. the entity widget's per-role "fonts" overrides).
        _allowed_field_types = (
            str,
            int,
            float,
            bool,
            list,
            dict,
            type(None),
        )
        for widget in body:
            if not isinstance(widget, dict):
                raise web.HTTPBadRequest(text="Each widget must be an object")
            if widget.get("type") not in valid_types:
                raise web.HTTPBadRequest(
                    text=f"Invalid widget type: {widget.get('type')}"
                )
            for key, val in widget.items():
                if key != "type" and not isinstance(val, _allowed_field_types):
                    raise web.HTTPBadRequest(
                        text=f"Invalid value type for field '{key}'"
                    )

        data = entry.runtime_data
        await data.store.async_save(body)
        data.widgets = body
        if data.entity is None:
            raise web.HTTPServiceUnavailable()
        await data.entity.async_request_refresh(body)

        return web.json_response({"status": "ok"})


class EinkPublicImageView(HomeAssistantView):
    """Unauthenticated view serving the rendered PNG with ETag/304 support."""

    url = "/api/eink_dashboard/{entry_id}/image.png"
    name = "api:eink_dashboard:image"
    requires_auth = False

    async def get(self, request: web.Request, entry_id: str) -> web.Response:
        """Serve the latest rendered PNG, honouring If-None-Match."""
        return await _serve_image(request, entry_id, bmp_depth=None)


class EinkPublicBmpImageView(HomeAssistantView):
    """Unauthenticated view serving the rendered image as an uncompressed BMP.

    Intended for microcontrollers (e.g. ESP8266 with ESPHome's
    ``online_image``) that do not have enough RAM to decode PNG. The
    optional ``bits`` query parameter selects the bit depth: ``1``
    forces 1-bit black/white, ``8`` forces 8-bit grayscale, and the
    default picks 1-bit whenever the image is pure black and white.
    """

    url = "/api/eink_dashboard/{entry_id}/image.bmp"
    name = "api:eink_dashboard:image_bmp"
    requires_auth = False

    async def get(self, request: web.Request, entry_id: str) -> web.Response:
        """Serve the latest rendered image as BMP, honouring If-None-Match."""
        bits = request.query.get("bits", BmpDepth.AUTO.value)
        try:
            depth = BmpDepth(bits)
        except ValueError:
            raise web.HTTPBadRequest(
                text="bits must be one of: auto, 1, 8"
            ) from None
        return await _serve_image(request, entry_id, bmp_depth=depth)


def _update_battery_from_query(
    request: web.Request, entry: EinkDashboardConfigEntry
) -> None:
    """Forward ``batteryLevel``/``isCharging`` query params to the sensor.

    Args:
        request: The incoming image request.
        entry: The loaded config entry owning the battery sensor.
    """
    battery_str = request.query.get("batteryLevel")
    if battery_str is None:
        return
    try:
        level = max(0, min(100, int(float(battery_str))))
    except (ValueError, TypeError):
        return
    sensor = entry.runtime_data.battery_sensor
    if sensor is None:
        return
    is_charging = request.query.get("isCharging") == "1"
    sensor.update_battery(level, is_charging)
    _LOGGER.info(
        "E-Ink device request: battery=%d%%, charging=%s",
        level,
        is_charging,
    )


async def _serve_image(
    request: web.Request,
    entry_id: str,
    *,
    bmp_depth: BmpDepth | None,
) -> web.Response:
    """Serve the latest rendered image as PNG or BMP with ETag/304 support.

    Args:
        request: The incoming image request.
        entry_id: Config entry ID taken from the URL.
        bmp_depth: BMP bit depth to convert to, or None to serve the
            rendered PNG unchanged.

    Returns:
        A 200 response with the image body, or a 304 when the client's
        If-None-Match matches the current ETag.

    Raises:
        HTTPNotFound: The entry ID is unknown or not loaded.
        HTTPServiceUnavailable: No image has been rendered yet.
    """
    hass = request.app["hass"]
    entry = get_loaded_entry(hass, entry_id)
    if entry is None:
        raise web.HTTPNotFound()

    entity = entry.runtime_data.entity
    if entity is None:
        raise web.HTTPServiceUnavailable()
    if bmp_depth is None:
        image_bytes = await entity.async_image()
    else:
        image_bytes = await entity.async_image_bmp(bmp_depth)
    if image_bytes is None:
        raise web.HTTPServiceUnavailable()

    # Battery arrives on every poll, even when returning 304.
    _update_battery_from_query(request, entry)

    etag = entity.etag
    if etag is not None and bmp_depth is not None:
        # Distinct validator per representation: the BMP is derived
        # deterministically from the PNG, so it changes exactly when
        # the PNG's hash does.
        etag = f'{etag[:-1]}-bmp{bmp_depth.value}"'

    if_none_match = request.headers.get("If-None-Match")
    if (
        etag is not None
        and if_none_match is not None
        and if_none_match in ("*", etag)
    ):
        _LOGGER.info("ETag hit (%s), returning 304", etag)
        resp = web.Response(status=304)
        resp.headers["ETag"] = etag
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    _LOGGER.info("Serving image: %d bytes, etag=%s", len(image_bytes), etag)
    response = web.Response(
        body=image_bytes,
        content_type="image/png" if bmp_depth is None else "image/bmp",
    )
    response.headers["Cache-Control"] = "no-cache"
    if etag is not None:
        response.headers["ETag"] = etag
    return response
