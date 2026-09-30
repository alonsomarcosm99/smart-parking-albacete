"""Lambda de ingesta de eventos MQTT desde AWS IoT Core.

Recibe un evento por mensaje publicado en parking/{zone}/spot/{spotId}/status,
normaliza el payload, hace UPSERT en la tabla parking-state y, si el estado
cambia respecto al anterior, invoca asincronamente al agregador para refrescar
los KPIs de la zona.
"""
from __future__ import annotations

import json
import os
import time
from decimal import Decimal

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
STATE_TABLE = os.environ["STATE_TABLE"]
AGGREGATOR_FN = os.environ["AGGREGATOR_FN"]

_ddb = boto3.resource("dynamodb", region_name=REGION)
_table = _ddb.Table(STATE_TABLE)
_lambda = boto3.client("lambda", region_name=REGION)


def _to_decimal(value):
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_decimal(v) for v in value]
    return value


def lambda_handler(event, context):
    payload = event if isinstance(event, dict) else json.loads(event)
    spot_id = payload.get("spotId")
    zone_id = payload.get("zoneId")
    status = payload.get("status")
    if not (spot_id and zone_id and status):
        return {"ok": False, "reason": "payload incompleto", "raw": payload}

    item = {
        "spotId": spot_id,
        "zoneId": zone_id,
        "status": status,
        "lat": _to_decimal(payload.get("lat", 0.0)),
        "lon": _to_decimal(payload.get("lon", 0.0)),
        "street": payload.get("street", ""),
        "batteryLevel": _to_decimal(payload.get("batteryLevel", 100)),
        "confidence": _to_decimal(payload.get("confidence", 1.0)),
        "lastUpdated": payload.get("timestamp") or int(time.time() * 1000),
        "sensorType": payload.get("sensorType", "magnetic"),
    }

    prev = _table.get_item(Key={"spotId": spot_id}).get("Item") or {}
    _table.put_item(Item=item)

    status_changed = prev.get("status") != status
    if status_changed:
        _lambda.invoke(
            FunctionName=AGGREGATOR_FN,
            InvocationType="Event",
            Payload=json.dumps({"zoneId": zone_id}).encode("utf-8"),
        )

    return {"ok": True, "spotId": spot_id, "statusChanged": status_changed}
