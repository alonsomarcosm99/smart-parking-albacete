"""Regenera parking_zone_seed.json muestreando calles reales de OpenStreetMap.

Consulta la Overpass API filtrando ways con highway transitable y sin
restricciones de aparcamiento dentro de la BBOX del piloto. Para cada sub-zona
se ordenan las calles por proximidad al centroide operativo y se selecciona el
mid-point de las N mas cercanas, garantizando que cada plaza cae sobre asfalto
publico y no encima de parques, edificios del campus o del estadio.

Uso:
    python infra/regen_coords_from_osm.py

Tras la regeneracion hay que relanzar el simulador para que los nuevos
lat/lon/street se propaguen via MQTT a DynamoDB.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import requests

BBOX = {
    "south": 38.976059,
    "west": -1.858728,
    "north": 38.983215,
    "east": -1.846111,
}

# Centroides operativos aproximados de cada sub-zona (lat, lon).
# Z1 = Campus UCLM, Z2 = Estadio Carlos Belmonte, Z3 = Hospital + Facultades
# sanitarias, Z4 = corredor residencial sur (Av. de la Mancha).
ZONE_CENTROIDS = {
    "Z1-CAMPUS":      (38.97930, -1.85650),
    "Z2-DEPORTIVO":   (38.98100, -1.85200),
    "Z3-SANITARIO":   (38.98150, -1.84800),
    "Z4-RESIDENCIAL": (38.97720, -1.85100),
}

SPOTS_PER_ZONE = 10
MIN_WAY_LENGTH_M = 25  # descartar trozos minusculos (intersecciones, rampas)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_QUERY = f"""
[out:json][timeout:60];
(
  way["highway"~"^(residential|tertiary|secondary|unclassified|living_street)$"]({BBOX['south']},{BBOX['west']},{BBOX['north']},{BBOX['east']});
);
out geom;
""".strip()

HTTP_HEADERS = {
    "User-Agent": "smart-parking-albacete-seed/1.0 (alonso.marcos@alu.uclm.es)",
    "Accept": "application/json",
}


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    R = 6371000.0
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def way_length_m(geom: list[dict]) -> float:
    total = 0.0
    for i in range(len(geom) - 1):
        total += haversine_m(
            (geom[i]["lat"], geom[i]["lon"]),
            (geom[i + 1]["lat"], geom[i + 1]["lon"]),
        )
    return total


def way_centroid(geom: list[dict]) -> tuple[float, float]:
    lats = [p["lat"] for p in geom]
    lons = [p["lon"] for p in geom]
    return sum(lats) / len(lats), sum(lons) / len(lons)


def point_at_fraction(geom: list[dict], fraction: float) -> tuple[float, float]:
    """Devuelve (lat, lon) a la fraccion [0,1] de longitud acumulada del way."""
    total = way_length_m(geom)
    if total <= 0:
        return geom[0]["lat"], geom[0]["lon"]
    target = total * fraction
    acc = 0.0
    for i in range(len(geom) - 1):
        a = (geom[i]["lat"], geom[i]["lon"])
        b = (geom[i + 1]["lat"], geom[i + 1]["lon"])
        seg = haversine_m(a, b)
        if acc + seg >= target:
            t = (target - acc) / seg if seg > 0 else 0
            return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        acc += seg
    return geom[-1]["lat"], geom[-1]["lon"]


def fetch_ways() -> list[dict]:
    print(f"[overpass] consultando {OVERPASS_URL} ...")
    resp = requests.post(
        OVERPASS_URL,
        data={"data": OVERPASS_QUERY},
        headers=HTTP_HEADERS,
        timeout=90,
    )
    if resp.status_code != 200:
        print(f"[overpass] status={resp.status_code} body={resp.text[:500]}")
    resp.raise_for_status()
    elements = resp.json().get("elements", [])
    ways = [
        e for e in elements
        if e.get("type") == "way" and e.get("geometry") and len(e["geometry"]) >= 2
    ]
    enriched = []
    for w in ways:
        length = way_length_m(w["geometry"])
        if length < MIN_WAY_LENGTH_M:
            continue
        w["_length_m"] = length
        w["_centroid"] = way_centroid(w["geometry"])
        enriched.append(w)
    print(f"[overpass] ways aptos tras filtrado: {len(enriched)}")
    return enriched


def pick_spots_for_zone(
    zone_id: str,
    centroid: tuple[float, float],
    ways: list[dict],
    n_spots: int,
) -> list[dict]:
    """Selecciona n_spots puntos sobre las calles mas cercanas al centroide."""
    ranked = sorted(ways, key=lambda w: haversine_m(w["_centroid"], centroid))
    selected: list[dict] = []
    used_ways: set[int] = set()

    # Primera pasada: una plaza por way (mid-point), priorizando ways cercanos.
    for w in ranked:
        if len(selected) >= n_spots:
            break
        if w["id"] in used_ways:
            continue
        lat, lon = point_at_fraction(w["geometry"], 0.5)
        if not _inside_bbox(lat, lon):
            continue
        selected.append({
            "way_id": w["id"],
            "lat": lat,
            "lon": lon,
            "street": (w.get("tags") or {}).get("name", "Calle sin nombre"),
        })
        used_ways.add(w["id"])

    # Segunda pasada: si faltan plazas, anadir puntos al 25 % y 75 % de los
    # ways mas largos ya utilizados.
    if len(selected) < n_spots:
        long_ways = sorted(
            (w for w in ranked if w["id"] in used_ways),
            key=lambda w: w["_length_m"],
            reverse=True,
        )
        for w in long_ways:
            if len(selected) >= n_spots:
                break
            for frac in (0.25, 0.75, 0.1, 0.9):
                if len(selected) >= n_spots:
                    break
                lat, lon = point_at_fraction(w["geometry"], frac)
                if not _inside_bbox(lat, lon):
                    continue
                selected.append({
                    "way_id": w["id"],
                    "lat": lat,
                    "lon": lon,
                    "street": (w.get("tags") or {}).get("name", "Calle sin nombre"),
                })

    return selected[:n_spots]


def _inside_bbox(lat: float, lon: float) -> bool:
    return (BBOX["south"] <= lat <= BBOX["north"]
            and BBOX["west"] <= lon <= BBOX["east"])


def main() -> None:
    ways = fetch_ways()
    if not ways:
        raise RuntimeError("Overpass no devolvio ninguna via apta dentro de la BBOX")

    spots_out: list[dict] = []
    for zone_id, centroid in ZONE_CENTROIDS.items():
        picks = pick_spots_for_zone(zone_id, centroid, ways, SPOTS_PER_ZONE)
        if len(picks) < SPOTS_PER_ZONE:
            print(f"[warn] {zone_id}: solo se obtuvieron {len(picks)} plazas")
        prefix = zone_id.split("-")[0]
        for i, p in enumerate(picks, start=1):
            spots_out.append({
                "spotId": f"ALB-{prefix}-{i:03d}",
                "zoneId": zone_id,
                "street": p["street"],
                "lat": round(p["lat"], 6),
                "lon": round(p["lon"], 6),
            })
        print(f"[{zone_id}] {len(picks)} plazas ubicadas")

    out_path = Path(__file__).resolve().parent / "parking_zone_seed.json"
    payload = {
        "_meta": {
            "description": (
                "Semilla regenerada via OpenStreetMap (Overpass API). Cada plaza "
                "se ubica sobre el mid-point (o cuarto/tres cuartos) de un way "
                "real con highway transitable, sin parking=no ni access privado, "
                "dentro de la BBOX del piloto. Garantiza que ningun punto cae "
                "sobre edificios, parques o zonas no asfaltadas."
            ),
            "bbox": {
                "sw": [BBOX["south"], BBOX["west"]],
                "ne": [BBOX["north"], BBOX["east"]],
            },
            "source": "OpenStreetMap contributors via Overpass API",
            "centroids_used": {z: list(c) for z, c in ZONE_CENTROIDS.items()},
        },
        "spots": spots_out,
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[ok] {out_path} regenerado con {len(spots_out)} plazas")


if __name__ == "__main__":
    main()
