"""Crea las tablas DynamoDB que respaldan el estado y los KPIs del piloto.

  * Tabla *parking-state*: estado actual de cada plaza (PK: spotId).
    Modelo single-table simple: una fila por plaza con sus atributos mas recientes.
  * Tabla *zone-kpis*: agregados por sub-zona y ventana temporal.
    PK: zoneId, SK: windowEnd (ISO 8601) para series temporales eficientes.

Ambas se crean en modo PAY_PER_REQUEST (on-demand) para evitar configurar
capacidad provisionada en un piloto con trafico irregular.
"""
from __future__ import annotations

from botocore.exceptions import ClientError

from common import (
    DYNAMO_KPIS_TABLE,
    DYNAMO_STATE_TABLE,
    is_not_found,
    load_state,
    log,
    save_state,
    session,
)


def create_state_table(ddb) -> None:
    try:
        ddb.describe_table(TableName=DYNAMO_STATE_TABLE)
        log(f"Tabla {DYNAMO_STATE_TABLE} ya existe")
        return
    except ClientError as err:
        if not is_not_found(err):
            raise
    ddb.create_table(
        TableName=DYNAMO_STATE_TABLE,
        AttributeDefinitions=[
            {"AttributeName": "spotId", "AttributeType": "S"},
        ],
        KeySchema=[{"AttributeName": "spotId", "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )
    waiter = ddb.get_waiter("table_exists")
    waiter.wait(TableName=DYNAMO_STATE_TABLE)
    log(f"Tabla {DYNAMO_STATE_TABLE} creada")


def create_kpis_table(ddb) -> None:
    try:
        ddb.describe_table(TableName=DYNAMO_KPIS_TABLE)
        log(f"Tabla {DYNAMO_KPIS_TABLE} ya existe")
        return
    except ClientError as err:
        if not is_not_found(err):
            raise
    ddb.create_table(
        TableName=DYNAMO_KPIS_TABLE,
        AttributeDefinitions=[
            {"AttributeName": "zoneId", "AttributeType": "S"},
            {"AttributeName": "windowEnd", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "zoneId", "KeyType": "HASH"},
            {"AttributeName": "windowEnd", "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    waiter = ddb.get_waiter("table_exists")
    waiter.wait(TableName=DYNAMO_KPIS_TABLE)
    log(f"Tabla {DYNAMO_KPIS_TABLE} creada")


def main() -> None:
    ddb = session().client("dynamodb")
    create_state_table(ddb)
    create_kpis_table(ddb)
    state = load_state()
    state.update(
        {
            "ddbStateTable": DYNAMO_STATE_TABLE,
            "ddbKpisTable": DYNAMO_KPIS_TABLE,
        }
    )
    save_state(state)
    log("02_setup_dynamodb completado")


if __name__ == "__main__":
    main()
