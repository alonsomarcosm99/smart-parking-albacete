"""Datos deterministas para la publicación gratuita del dashboard."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _unit_interval(value: str) -> float:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64 - 1)


def _occupancy_rate(zone_id: str, instant: datetime) -> float:
    hour = instant.hour + instant.minute / 60
    if zone_id == "Z1-CAMPUS":
        return 0.72 if 8 <= hour < 19 else 0.18
    if zone_id == "Z2-DEPORTIVO":
        return 0.68 if 17 <= hour < 23 else 0.28
    if zone_id == "Z3-SANITARIO":
        return 0.62
    if zone_id == "Z4-RESIDENCIAL":
        return 0.76 if hour >= 20 or hour < 8 else 0.34
    return 0.5


def _load_spots(seed_path: Path) -> list[dict]:
    with seed_path.open(encoding="utf-8") as stream:
        payload = json.load(stream)
    return payload["spots"]


def build_demo_snapshot(seed_path: Path, now: datetime | None = None) -> dict:
    """Genera estado actual y serie temporal sin depender de AWS."""

    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    minute_bucket = int(instant.timestamp() // 60)
    spots: list[dict] = []

    for index, source in enumerate(_load_spots(seed_path)):
        spot = dict(source)
        spot_id = spot["spotId"]
        unavailable = _unit_interval(f"health:{spot_id}:{minute_bucket // 15}") < 0.035
        occupied = _unit_interval(f"status:{spot_id}:{minute_bucket}") < _occupancy_rate(
            spot["zoneId"], instant
        )
        spot.update(
            status="unknown" if unavailable else ("occupied" if occupied else "free"),
            batteryLevel=max(18, 97 - ((index * 7 + instant.timetuple().tm_yday) % 73)),
            confidence=0.0 if unavailable else round(0.88 + _unit_interval(f"confidence:{spot_id}") * 0.11, 2),
            lastUpdated=int(instant.timestamp() * 1000),
        )
        spots.append(spot)

    zone_ids = sorted({spot["zoneId"] for spot in spots})
    zones = []
    history: dict[str, list[dict]] = {}
    for zone_id in zone_ids:
        zone_spots = [spot for spot in spots if spot["zoneId"] == zone_id]
        counts = {
            state: sum(1 for spot in zone_spots if spot["status"] == state)
            for state in ("free", "occupied", "unknown")
        }
        zones.append({"zoneId": zone_id, **counts})

        points = []
        for offset in range(47, -1, -1):
            point_time = instant - timedelta(minutes=offset * 10)
            wave = math.sin((point_time.timestamp() / 3600) + len(zone_id)) * 0.08
            jitter = (_unit_interval(f"history:{zone_id}:{int(point_time.timestamp() // 600)}") - 0.5) * 0.08
            rate = min(0.98, max(0.02, _occupancy_rate(zone_id, point_time) + wave + jitter))
            points.append({"windowEnd": point_time.isoformat(), "occupancyRate": round(rate, 3)})
        history[zone_id] = points

    return {"generatedAt": instant.isoformat(), "spots": spots, "zones": zones, "history": history}
