"""Constantes y utilidades compartidas por los scripts de infraestructura.

Este modulo centraliza los nombres de recursos, la region y los helpers de boto3
para que un cambio de prefijo o de region se propague a todos los scripts.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
RESOURCE_PREFIX = os.environ.get("RESOURCE_PREFIX", "smart-parking-albacete")

# Nombres de recursos derivados del prefijo (idempotentes)
THING_TYPE_NAME = f"{RESOURCE_PREFIX}-sensor-type"
THING_GROUP_NAME = f"{RESOURCE_PREFIX}-fleet"
POLICY_NAME = f"{RESOURCE_PREFIX}-sensor-policy"
RULE_NAME = RESOURCE_PREFIX.replace("-", "_") + "_ingest_rule"
DYNAMO_STATE_TABLE = f"{RESOURCE_PREFIX}-state"
DYNAMO_KPIS_TABLE = f"{RESOURCE_PREFIX}-zone-kpis"
LAMBDA_INGEST = f"{RESOURCE_PREFIX}-ingest"
LAMBDA_AGGREGATOR = f"{RESOURCE_PREFIX}-aggregator"
LAMBDA_API = f"{RESOURCE_PREFIX}-api"
API_NAME = f"{RESOURCE_PREFIX}-api"
S3_RAW_BUCKET = f"{RESOURCE_PREFIX}-raw-{int(__import__('hashlib').md5(RESOURCE_PREFIX.encode()).hexdigest()[:6], 16)}"
LAB_ROLE_NAME = "LabRole"

# Topic MQTT usado por el simulador
MQTT_TOPIC_TEMPLATE = "parking/{zone}/spot/{spot}/status"
MQTT_TOPIC_WILDCARD = "parking/+/spot/+/status"

# Rutas locales
INFRA_DIR = Path(__file__).resolve().parent
PROTOTYPE_DIR = INFRA_DIR.parent
CERTS_DIR = PROTOTYPE_DIR / "simulator" / "certs"
SEED_FILE = INFRA_DIR / "parking_zone_seed.json"
STATE_FILE = INFRA_DIR / "infra_state.json"


def session() -> boto3.Session:
    """Crea una sesion boto3 reutilizable con la region configurada."""
    return boto3.Session(region_name=AWS_REGION)


def lab_role_arn() -> str:
    """Devuelve el ARN del LabRole de AWS Academy."""
    iam = session().client("iam")
    role = iam.get_role(RoleName=LAB_ROLE_NAME)
    return role["Role"]["Arn"]


def account_id() -> str:
    """Devuelve el AWS Account ID asociado al perfil activo."""
    sts = session().client("sts")
    return sts.get_caller_identity()["Account"]


def iot_endpoint() -> str:
    """Devuelve el endpoint MQTT-ATS del data plane de AWS IoT Core."""
    iot = session().client("iot")
    return iot.describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]


def load_seed() -> dict[str, Any]:
    """Lee el fichero de plazas con coordenadas reales del piloto."""
    with SEED_FILE.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_state() -> dict[str, Any]:
    """Estado persistente de la infraestructura desplegada (idempotencia)."""
    if not STATE_FILE.exists():
        return {}
    with STATE_FILE.open("r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def save_state(state: dict[str, Any]) -> None:
    with STATE_FILE.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)


def is_not_found(err: ClientError) -> bool:
    code = err.response.get("Error", {}).get("Code", "")
    return code in {
        "ResourceNotFoundException",
        "NoSuchEntity",
        "NotFoundException",
        "NoSuchEntityException",
        "404",
    }


def log(msg: str) -> None:
    print(f"[infra] {msg}")
