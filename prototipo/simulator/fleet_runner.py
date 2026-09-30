"""Orquesta una flota de sensores simulados de smart parking.

Carga la semilla con coordenadas reales, decide patrones realistas por zona y
arranca un hilo MQTT por sensor. Inyecta intencionadamente algunas anomalias
(bateria baja, sensor caido) para poder demostrarlas en la defensa.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "infra"))

from common import CERTS_DIR, SEED_FILE, iot_endpoint, log  # noqa: E402

from parking_sensor import ZONE_PROFILES, ParkingSensor  # noqa: E402


def _resolve_endpoint() -> str:
    state_file = ROOT / "infra" / "infra_state.json"
    if state_file.exists():
        with state_file.open(encoding="utf-8") as fh:
            state = json.load(fh)
        if state.get("iotEndpoint"):
            return state["iotEndpoint"]
    return iot_endpoint()


def _intensity(now: datetime, profile) -> tuple[float, float]:
    """Aplica multiplicadores horarios/semana al ritmo base."""
    hour = now.hour
    arrival = profile.base_arrival_rate
    departure = profile.base_departure_rate
    if 8 <= hour <= 10:
        arrival *= profile.morning_peak
    if 16 <= hour <= 18:
        arrival *= profile.afternoon_peak
        departure *= 0.6
    if now.weekday() >= 5:
        arrival *= profile.weekend_peak
    return arrival, departure


def build_fleet(num_spots: int, heartbeat: int) -> list[ParkingSensor]:
    with SEED_FILE.open(encoding="utf-8") as fh:
        seed = json.load(fh)
    spots = seed["spots"][:num_spots]

    endpoint = _resolve_endpoint()
    cert = CERTS_DIR / "device.cert.pem"
    key = CERTS_DIR / "device.private.key"
    ca = CERTS_DIR / "AmazonRootCA1.pem"

    fleet: list[ParkingSensor] = []
    for i, spot in enumerate(spots):
        fleet.append(
            ParkingSensor(
                spot_id=spot["spotId"],
                zone_id=spot["zoneId"],
                street=spot["street"],
                lat=spot["lat"],
                lon=spot["lon"],
                endpoint=endpoint,
                cert_path=cert,
                key_path=key,
                ca_path=ca,
                heartbeat_seconds=heartbeat,
                fault=(i == len(spots) - 1),         # ultimo sensor caido
                low_battery=(i % 9 == 0),            # ~1 de cada 9 con bateria baja
            )
        )
    return fleet


def run(num_spots: int, duration: int, heartbeat: int, tick: float) -> None:
    fleet = build_fleet(num_spots, heartbeat)
    log(f"Construida flota de {len(fleet)} sensores")

    # Conexion en paralelo (no tarda nada)
    for sensor in fleet:
        try:
            sensor.connect()
        except Exception as err:
            log(f"[{sensor.spot_id}] error conectando: {err}")

    # Publicacion inicial para sembrar el estado en DynamoDB
    for sensor in fleet:
        sensor.publish(force=True)
    log("Publicacion inicial enviada (estado semilla)")

    started = time.time()
    try:
        while True:
            now = datetime.now()
            for sensor in fleet:
                profile = ZONE_PROFILES.get(sensor.zone_id)
                if profile is None:
                    continue
                arrival, departure = _intensity(now, profile)
                sensor.step(arrival, departure, tick)
            time.sleep(tick)
            if duration and (time.time() - started) >= duration:
                break
    except KeyboardInterrupt:
        log("Interrumpido por usuario")
    finally:
        for sensor in fleet:
            try:
                sensor.disconnect()
            except Exception:
                pass
        log("Flota desconectada limpiamente")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--num-spots", type=int, default=40)
    parser.add_argument(
        "--duration",
        type=int,
        default=0,
        help="Segundos a ejecutar (0 = indefinido)",
    )
    parser.add_argument("--heartbeat", type=int, default=60)
    parser.add_argument("--tick", type=float, default=2.0)
    args = parser.parse_args()

    random.seed(42)
    run(args.num_spots, args.duration, args.heartbeat, args.tick)


if __name__ == "__main__":
    main()
