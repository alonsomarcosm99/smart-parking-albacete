"""Cliente MQTT/TLS que representa un sensor magnetico de plaza.

Cada sensor mantiene un estado (free/occupied) que cambia segun una cadena de
Markov simple parametrizada por la "presion" de la zona. Publica un evento
cada vez que el estado cambia (con debounce minimo) y un heartbeat periodico
mientras nadie llega para que el agregador pueda detectar perdidas de senal.

Conexion mTLS contra el endpoint Data-ATS de AWS IoT Core con los certificados
generados por 01_setup_iot_core.py.
"""
from __future__ import annotations

import json
import random
import ssl
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Thread

import paho.mqtt.client as mqtt


@dataclass
class ZoneProfile:
    """Parametros que controlan el comportamiento del sensor en cada sub-zona."""

    base_arrival_rate: float       # eventos / minuto (medio dia laboral)
    base_departure_rate: float
    morning_peak: float = 1.0      # multiplicador 8h-10h
    afternoon_peak: float = 1.0    # multiplicador 16h-18h
    weekend_peak: float = 1.0      # multiplicador sabado/domingo


ZONE_PROFILES: dict[str, ZoneProfile] = {
    "Z1-CAMPUS":     ZoneProfile(0.8, 0.5, morning_peak=2.5, afternoon_peak=2.0),
    "Z2-DEPORTIVO":  ZoneProfile(0.3, 0.2, afternoon_peak=1.5, weekend_peak=3.0),
    "Z3-SANITARIO":  ZoneProfile(1.0, 1.0, morning_peak=1.3, afternoon_peak=1.3),
    "Z4-RESIDENCIAL": ZoneProfile(0.4, 0.4, morning_peak=1.2),
}


@dataclass
class ParkingSensor:
    spot_id: str
    zone_id: str
    street: str
    lat: float
    lon: float
    endpoint: str
    cert_path: Path
    key_path: Path
    ca_path: Path
    heartbeat_seconds: int = 60
    fault: bool = False           # True => solo publica un estado inicial unknown
    low_battery: bool = False

    _client: mqtt.Client = field(init=False, repr=False)
    _status: str = field(default="free", init=False)
    _battery: float = field(default=100.0, init=False)
    _stop: Event = field(default_factory=Event, init=False)
    _last_publish: float = field(default=0.0, init=False)
    _connected: Event = field(default_factory=Event, init=False)

    def __post_init__(self) -> None:
        self._battery = random.uniform(25, 35) if self.low_battery else random.uniform(70, 100)
        self._status = "unknown" if self.fault else random.choice(["free", "occupied", "free"])
        self._client = mqtt.Client(
            client_id=self.spot_id,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            protocol=mqtt.MQTTv5,
        )
        self._client.tls_set(
            ca_certs=str(self.ca_path),
            certfile=str(self.cert_path),
            keyfile=str(self.key_path),
            tls_version=ssl.PROTOCOL_TLSv1_2,
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            self._connected.set()
        else:
            print(f"[{self.spot_id}] conexion fallida rc={reason_code}")

    def _on_disconnect(self, client, userdata, *args, **kwargs):
        self._connected.clear()

    def connect(self) -> None:
        self._client.connect(self.endpoint, port=8883, keepalive=120)
        self._client.loop_start()
        self._connected.wait(timeout=10)

    def disconnect(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def _topic(self) -> str:
        return f"parking/{self.zone_id}/spot/{self.spot_id}/status"

    def _build_payload(self) -> dict:
        return {
            "spotId": self.spot_id,
            "zoneId": self.zone_id,
            "street": self.street,
            "lat": self.lat,
            "lon": self.lon,
            "status": self._status,
            "batteryLevel": round(self._battery, 1),
            "confidence": round(random.uniform(0.88, 0.99), 3),
            "sensorType": "magnetic",
            "timestamp": int(time.time() * 1000),
        }

    def publish(self, *, force: bool = False) -> None:
        if self.fault and not force:
            return
        payload = self._build_payload()
        self._client.publish(self._topic(), json.dumps(payload), qos=1)
        self._last_publish = time.time()

    def step(self, arrival_rate: float, departure_rate: float, tick_seconds: float) -> None:
        """Evoluciona el estado del sensor durante `tick_seconds`."""
        if self.fault:
            return
        prob_change = (
            arrival_rate if self._status == "free" else departure_rate
        ) * (tick_seconds / 60.0)
        if random.random() < prob_change:
            self._status = "occupied" if self._status == "free" else "free"
            self.publish()
        elif time.time() - self._last_publish > self.heartbeat_seconds:
            self.publish()  # heartbeat
        # Drenaje de bateria muy lento (simulado)
        self._battery = max(0.0, self._battery - random.uniform(0.0005, 0.002))

    def status(self) -> str:
        return "FAULT" if self.fault else self._status
