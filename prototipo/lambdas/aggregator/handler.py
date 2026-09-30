"""Lambda que recalcula los KPIs de una zona y los persiste como serie temporal.

Se invoca tras cada cambio de estado significativo. Hace un Scan filtrado por
zoneId (pequeno volumen en el piloto) y escribe una fila en zone-kpis con la
marca temporal. En produccion se sustituiria por una consulta a un GSI o por
una ventana de Apache Flink, pero para el piloto el coste es despreciable.
"""
from __future__ import annotations

import json
import os
import time
from decimal import Decimal
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Attr

REGION = os.environ.get("AWS_REGION", "us-east-1")
STATE_TABLE = os.environ["STATE_TABLE"]
KPIS_TABLE = os.environ["KPIS_TABLE"]

_ddb = boto3.resource("dynamodb", region_name=REGION)
_state = _ddb.Table(STATE_TABLE)
_kpis = _ddb.Table(KPIS_TABLE)


def _scan_zone(zone_id: str) -> list[dict]:
    items: list[dict] = []
    kwargs = {"FilterExpression": Attr("zoneId").eq(zone_id)}
    while True:
        resp = _state.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def lambda_handler(event, context):
    zone_id = (event or {}).get("zoneId")
    if not zone_id:
        return {"ok": False, "reason": "zoneId requerido"}

    spots = _scan_zone(zone_id)
    total = len(spots)
    free = sum(1 for s in spots if s.get("status") == "free")
    occupied = sum(1 for s in spots if s.get("status") == "occupied")
    unknown = total - free - occupied
    occupancy_rate = (occupied / total) if total else 0.0

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    item = {
        "zoneId": zone_id,
        "windowEnd": now,
        "totalSpots": total,
        "freeSpots": free,
        "occupiedSpots": occupied,
        "unknownSpots": unknown,
        "occupancyRate": Decimal(str(round(occupancy_rate, 4))),
        "computedAtMs": int(time.time() * 1000),
    }
    _kpis.put_item(Item=item)
    return {"ok": True, "zoneId": zone_id, "freeSpots": free, "occupiedSpots": occupied}
