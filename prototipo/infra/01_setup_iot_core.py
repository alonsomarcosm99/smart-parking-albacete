"""Aprovisiona los recursos necesarios en AWS IoT Core.

Crea:
  * Un Thing Type y un Thing Group (organizativo).
  * Un Thing por cada plaza definida en el seed.
  * Un unico certificado X.509 compartido por toda la flota piloto (suficiente
    para demostrar autenticacion mTLS sin proliferar certificados).
  * Una IoT Policy permisiva sobre los topics del proyecto.
  * Adjunta el certificado y la policy a cada Thing.

Es idempotente: si los recursos ya existen los reutiliza y solo crea lo nuevo.
Persiste su estado en infra_state.json para que el teardown sepa que borrar.
"""
from __future__ import annotations

import json
from pathlib import Path

import urllib.request

from botocore.exceptions import ClientError

from common import (
    CERTS_DIR,
    POLICY_NAME,
    THING_GROUP_NAME,
    THING_TYPE_NAME,
    iot_endpoint,
    is_not_found,
    load_seed,
    load_state,
    log,
    save_state,
    session,
)


AMAZON_ROOT_CA_URL = (
    "https://www.amazontrust.com/repository/AmazonRootCA1.pem"
)


def ensure_thing_type(iot) -> None:
    try:
        resp = iot.describe_thing_type(thingTypeName=THING_TYPE_NAME)
        status = resp.get("thingTypeMetadata", {}).get("thingTypeStatus")
        if status == "DEPRECATED":
            raise RuntimeError(
                f"Thing type {THING_TYPE_NAME} esta DEPRECATED. "
                "AWS IoT no permite crear Things con un tipo deprecated. "
                "Espera 5 minutos desde el teardown, borralo con "
                f"`aws iot delete-thing-type --thing-type-name {THING_TYPE_NAME}` "
                "y relanza este script."
            )
        log(f"Thing type {THING_TYPE_NAME} ya existe")
    except ClientError as err:
        if not is_not_found(err):
            raise
        iot.create_thing_type(
            thingTypeName=THING_TYPE_NAME,
            thingTypeProperties={
                "thingTypeDescription": "Sensor magnetico de plaza de aparcamiento",
                "searchableAttributes": ["zoneId", "street"],
            },
        )
        log(f"Thing type {THING_TYPE_NAME} creado")


def ensure_thing_group(iot) -> None:
    try:
        iot.describe_thing_group(thingGroupName=THING_GROUP_NAME)
        log(f"Thing group {THING_GROUP_NAME} ya existe")
    except ClientError as err:
        if not is_not_found(err):
            raise
        iot.create_thing_group(
            thingGroupName=THING_GROUP_NAME,
            thingGroupProperties={
                "thingGroupDescription": "Flota completa de sensores del piloto smart parking",
            },
        )
        log(f"Thing group {THING_GROUP_NAME} creado")


def ensure_policy(iot) -> None:
    document = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": "iot:Connect", "Resource": "*"},
            {
                "Effect": "Allow",
                "Action": "iot:Publish",
                "Resource": "arn:aws:iot:*:*:topic/parking/*",
            },
            {
                "Effect": "Allow",
                "Action": "iot:Subscribe",
                "Resource": "arn:aws:iot:*:*:topicfilter/parking/*",
            },
            {
                "Effect": "Allow",
                "Action": "iot:Receive",
                "Resource": "arn:aws:iot:*:*:topic/parking/*",
            },
        ],
    }
    try:
        iot.get_policy(policyName=POLICY_NAME)
        log(f"IoT policy {POLICY_NAME} ya existe")
    except ClientError as err:
        if not is_not_found(err):
            raise
        iot.create_policy(
            policyName=POLICY_NAME,
            policyDocument=json.dumps(document),
        )
        log(f"IoT policy {POLICY_NAME} creada")


def _sanitize_attr(value: str) -> str:
    """AWS IoT Thing attributes solo aceptan [a-zA-Z0-9_.,@/:#=\\[\\]-] y no admiten espacios.
    Sustituimos caracteres invalidos por guion bajo y truncamos a 256 chars."""
    import re

    clean = re.sub(r"[^a-zA-Z0-9_.,@/:#=\[\]\-]", "_", value)
    return clean[:256]


def ensure_things(iot, spots: list[dict]) -> list[str]:
    created = []
    for spot in spots:
        thing_name = spot["spotId"]
        attrs = {
            "zoneId": _sanitize_attr(spot["zoneId"]),
            "street": _sanitize_attr(spot["street"][:64]),
            "lat": _sanitize_attr(str(spot["lat"])),
            "lon": _sanitize_attr(str(spot["lon"])),
        }
        try:
            iot.describe_thing(thingName=thing_name)
        except ClientError as err:
            if not is_not_found(err):
                raise
            iot.create_thing(
                thingName=thing_name,
                thingTypeName=THING_TYPE_NAME,
                attributePayload={"attributes": attrs, "merge": False},
            )
            created.append(thing_name)
        # Asegura pertenencia al grupo (no falla si ya esta dentro)
        try:
            iot.add_thing_to_thing_group(
                thingGroupName=THING_GROUP_NAME,
                thingName=thing_name,
            )
        except ClientError:
            pass
    log(f"Things en flota: {len(spots)} (creados nuevos: {len(created)})")
    return [s["spotId"] for s in spots]


def ensure_certificate(iot, state: dict) -> dict:
    """Crea (una sola vez) el certificado compartido y lo guarda en disco."""
    if "certificateId" in state and (CERTS_DIR / "device.cert.pem").exists():
        log(f"Certificado reutilizado: {state['certificateId']}")
        return state

    CERTS_DIR.mkdir(parents=True, exist_ok=True)
    keys = iot.create_keys_and_certificate(setAsActive=True)
    cert_id = keys["certificateId"]
    cert_arn = keys["certificateArn"]
    (CERTS_DIR / "device.cert.pem").write_text(keys["certificatePem"], encoding="utf-8")
    (CERTS_DIR / "device.private.key").write_text(
        keys["keyPair"]["PrivateKey"], encoding="utf-8"
    )
    # Descarga la CA raiz de Amazon (necesaria para el handshake TLS)
    with urllib.request.urlopen(AMAZON_ROOT_CA_URL, timeout=15) as resp:
        ca_pem = resp.read().decode("utf-8")
    (CERTS_DIR / "AmazonRootCA1.pem").write_text(ca_pem, encoding="utf-8")
    log(f"Certificado creado: {cert_id} (ficheros en {CERTS_DIR})")

    state["certificateId"] = cert_id
    state["certificateArn"] = cert_arn
    return state


def attach_policy_and_things(iot, cert_arn: str, things: list[str]) -> None:
    try:
        iot.attach_policy(policyName=POLICY_NAME, target=cert_arn)
    except ClientError:
        pass
    for thing in things:
        try:
            iot.attach_thing_principal(thingName=thing, principal=cert_arn)
        except ClientError:
            pass
    log(f"Policy y {len(things)} things vinculados al certificado")


def main() -> None:
    state = load_state()
    seed = load_seed()
    spots = seed["spots"]

    iot = session().client("iot")
    endpoint = iot_endpoint()
    log(f"Endpoint IoT Data-ATS: {endpoint}")

    ensure_thing_type(iot)
    ensure_thing_group(iot)
    ensure_policy(iot)
    things = ensure_things(iot, spots)
    state = ensure_certificate(iot, state)
    attach_policy_and_things(iot, state["certificateArn"], things)

    state.update(
        {
            "iotEndpoint": endpoint,
            "things": things,
            "policyName": POLICY_NAME,
            "thingGroupName": THING_GROUP_NAME,
            "thingTypeName": THING_TYPE_NAME,
        }
    )
    save_state(state)
    log("01_setup_iot_core completado")


if __name__ == "__main__":
    main()
