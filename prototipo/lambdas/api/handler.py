"""Lambda detras de API Gateway que sirve la informacion al dashboard y a terceros.

Endpoints servidos:
  GET /spots                 -> lista de plazas (estado actual)
  GET /spots/{spotId}        -> detalle de una plaza
  GET /zones                 -> lista de sub-zonas con KPIs mas recientes
  GET /zones/{zoneId}/kpis   -> serie temporal de KPIs (ultimas N filas)

El parametro de query string ?format=geojson devuelve la coleccion en formato
GeoJSON FeatureCollection (consumible directamente por mapas/clientes terceros).
"""
from __future__ import annotations

import json
import os
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

REGION = os.environ.get("AWS_REGION", "us-east-1")
STATE_TABLE = os.environ["STATE_TABLE"]
KPIS_TABLE = os.environ["KPIS_TABLE"]

_ddb = boto3.resource("dynamodb", region_name=REGION)
_state = _ddb.Table(STATE_TABLE)
_kpis = _ddb.Table(KPIS_TABLE)


class _DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            if obj % 1 == 0:
                return int(obj)
            return float(obj)
        return super().default(obj)


def _response(body, status: int = 200, headers: dict | None = None):
    base_headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Allow-Methods": "GET,OPTIONS",
    }
    if headers:
        base_headers.update(headers)
    return {
        "statusCode": status,
        "headers": base_headers,
        "body": json.dumps(body, cls=_DecimalEncoder, ensure_ascii=False),
    }


def _scan_all(table):
    items: list[dict] = []
    kwargs: dict = {}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def _to_geojson(spots: list[dict]) -> dict:
    color = {"free": "#2ecc71", "occupied": "#e74c3c"}
    features = []
    for s in spots:
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(s.get("lon", 0)), float(s.get("lat", 0))],
                },
                "properties": {
                    "spotId": s.get("spotId"),
                    "zoneId": s.get("zoneId"),
                    "status": s.get("status"),
                    "color": color.get(s.get("status"), "#7f8c8d"),
                    "batteryLevel": s.get("batteryLevel"),
                    "lastUpdated": s.get("lastUpdated"),
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def lambda_handler(event, context):
    method = event.get("httpMethod", "GET")
    if method == "OPTIONS":
        return _response({"ok": True})

    path = event.get("path", "/")
    params = event.get("pathParameters") or {}
    qs = event.get("queryStringParameters") or {}

    if path == "/spots":
        spots = _scan_all(_state)
        zone_filter = qs.get("zone")
        if zone_filter:
            spots = [s for s in spots if s.get("zoneId") == zone_filter]
        if qs.get("format") == "geojson":
            return _response(_to_geojson(spots))
        return _response({"count": len(spots), "items": spots})

    if path.startswith("/spots/") and params.get("spotId"):
        item = _state.get_item(Key={"spotId": params["spotId"]}).get("Item")
        if not item:
            return _response({"error": "spot not found"}, status=404)
        return _response(item)

    if path == "/zones":
        spots = _scan_all(_state)
        zones: dict[str, dict] = {}
        for s in spots:
            zid = s.get("zoneId")
            agg = zones.setdefault(
                zid,
                {"zoneId": zid, "total": 0, "free": 0, "occupied": 0, "unknown": 0},
            )
            agg["total"] += 1
            if s.get("status") == "free":
                agg["free"] += 1
            elif s.get("status") == "occupied":
                agg["occupied"] += 1
            else:
                agg["unknown"] += 1
        for agg in zones.values():
            agg["occupancyRate"] = round(
                (agg["occupied"] / agg["total"]) if agg["total"] else 0, 4
            )
        return _response({"count": len(zones), "items": list(zones.values())})

    if path.startswith("/zones/") and params.get("zoneId") and path.endswith("/kpis"):
        limit = int(qs.get("limit", "100"))
        resp = _kpis.query(
            KeyConditionExpression=Key("zoneId").eq(params["zoneId"]),
            ScanIndexForward=False,
            Limit=limit,
        )
        items = resp.get("Items", [])
        return _response({"count": len(items), "items": items})

    return _response({"error": "route not found", "path": path}, status=404)
