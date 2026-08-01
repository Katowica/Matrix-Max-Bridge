from __future__ import annotations

import copy
import logging
from typing import Any

logger = logging.getLogger(__name__)


def apply_nio_schema_patches() -> None:
    from nio.events import room_events
    from nio.schemas import Schemas

    rc_content = Schemas.room_create["properties"]["content"]
    req = list(rc_content.get("required", []))
    if "creator" in req:
        rc_content["required"] = [r for r in req if r != "creator"]

    _orig = room_events.RoomCreateEvent.from_dict.__func__

    @classmethod
    def _room_create_from_dict(
        cls: Any,
        parsed_dict: dict[str, Any],
    ) -> Any:
        pd = copy.deepcopy(parsed_dict)
        content = pd.setdefault("content", {})
        if "creator" not in content:
            content["creator"] = pd.get("sender") or ""
        if "m.federate" not in content:
            content["m.federate"] = True
        if "room_version" not in content:
            content["room_version"] = "1"
        return _orig(cls, pd)

    room_events.RoomCreateEvent.from_dict = _room_create_from_dict
