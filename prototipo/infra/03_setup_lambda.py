"""Empaqueta y despliega las tres Lambdas y conecta la regla IoT.

Las Lambdas usan solo la libreria estandar y boto3 (incluido en el runtime),
asi que no es necesario instalar dependencias en el paquete. Se generan tres
zips independientes y se publican como funciones con runtime python3.12 y rol
LabRole.

Tambien crea la IoT Topic Rule que filtra los mensajes MQTT y los redirige a
la Lambda de ingesta, asegurando los permisos `lambda:InvokeFunction` que IoT
necesita para hacer dicha invocacion.
"""
from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

from botocore.exceptions import ClientError

from common import (
    AWS_REGION,
    DYNAMO_KPIS_TABLE,
    DYNAMO_STATE_TABLE,
    LAMBDA_AGGREGATOR,
    LAMBDA_API,
    LAMBDA_INGEST,
    MQTT_TOPIC_WILDCARD,
    PROTOTYPE_DIR,
    RULE_NAME,
    account_id,
    is_not_found,
    lab_role_arn,
    load_state,
    log,
    save_state,
    session,
)

LAMBDA_RUNTIME = "python3.12"
LAMBDA_TIMEOUT = 15
LAMBDA_MEMORY = 256


def _zip_handler(folder: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(folder / "handler.py", arcname="handler.py")
    buf.seek(0)
    return buf.read()


def _wait_function_ready(lc, name: str) -> None:
    """Espera a que la Lambda este en estado Active/Successful (no Pending/InProgress)."""
    for _ in range(40):
        cfg = lc.get_function(FunctionName=name)["Configuration"]
        last = cfg.get("LastUpdateStatus", "Successful")
        state = cfg.get("State", "Active")
        if state == "Active" and last in {"Successful", None}:
            return
        if last == "Failed" or state == "Failed":
            raise RuntimeError(f"Lambda {name} en estado fallido: {cfg}")
        time.sleep(3)


def _upsert_function(lc, name: str, zip_bytes: bytes, env: dict, role_arn: str) -> str:
    try:
        lc.get_function(FunctionName=name)
        _wait_function_ready(lc, name)
        lc.update_function_code(FunctionName=name, ZipFile=zip_bytes, Publish=True)
        _wait_function_ready(lc, name)
        lc.update_function_configuration(
            FunctionName=name,
            Environment={"Variables": env},
            Timeout=LAMBDA_TIMEOUT,
            MemorySize=LAMBDA_MEMORY,
            Role=role_arn,
        )
        _wait_function_ready(lc, name)
        log(f"Lambda {name} actualizada")
    except ClientError as err:
        if not is_not_found(err):
            raise
        # Reintento corto por si el rol acaba de crearse
        for _ in range(6):
            try:
                lc.create_function(
                    FunctionName=name,
                    Runtime=LAMBDA_RUNTIME,
                    Role=role_arn,
                    Handler="handler.lambda_handler",
                    Code={"ZipFile": zip_bytes},
                    Timeout=LAMBDA_TIMEOUT,
                    MemorySize=LAMBDA_MEMORY,
                    Environment={"Variables": env},
                    Publish=True,
                )
                break
            except ClientError as ce:
                if "cannot be assumed by Lambda" in str(ce):
                    time.sleep(5)
                    continue
                raise
        _wait_function_ready(lc, name)
        log(f"Lambda {name} creada")
    arn = lc.get_function(FunctionName=name)["Configuration"]["FunctionArn"]
    return arn


def _ensure_iot_invoke_permission(lc, function_name: str, rule_arn: str) -> None:
    statement_id = f"iot-invoke-{function_name}"
    try:
        lc.add_permission(
            FunctionName=function_name,
            StatementId=statement_id,
            Action="lambda:InvokeFunction",
            Principal="iot.amazonaws.com",
            SourceArn=rule_arn,
        )
        log(f"Permiso de invocacion concedido a IoT sobre {function_name}")
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == "ResourceConflictException":
            return
        raise


def _ensure_topic_rule(iot, ingest_arn: str) -> str:
    payload = {
        "sql": f"SELECT * FROM '{MQTT_TOPIC_WILDCARD}'",
        "awsIotSqlVersion": "2016-03-23",
        "description": "Reglas para enrutar eventos de smart parking a la Lambda de ingesta",
        "ruleDisabled": False,
        "actions": [
            {
                "lambda": {"functionArn": ingest_arn},
            }
        ],
    }
    # El LabRole de AWS Academy puede no autorizar get_topic_rule; usamos
    # list_topic_rules (que requiere unicamente iot:ListTopicRules) para
    # decidir si toca create o replace.
    existing = {r["ruleName"] for r in iot.list_topic_rules().get("rules", [])}
    if RULE_NAME in existing:
        iot.replace_topic_rule(ruleName=RULE_NAME, topicRulePayload=payload)
        log(f"Topic rule {RULE_NAME} actualizada")
    else:
        iot.create_topic_rule(ruleName=RULE_NAME, topicRulePayload=payload)
        log(f"Topic rule {RULE_NAME} creada")
    return f"arn:aws:iot:{AWS_REGION}:{account_id()}:rule/{RULE_NAME}"


def main() -> None:
    role_arn = lab_role_arn()
    log(f"LabRole ARN: {role_arn}")

    lc = session().client("lambda")
    iot = session().client("iot")

    ingest_zip = _zip_handler(PROTOTYPE_DIR / "lambdas" / "ingest")
    agg_zip = _zip_handler(PROTOTYPE_DIR / "lambdas" / "aggregator")
    api_zip = _zip_handler(PROTOTYPE_DIR / "lambdas" / "api")

    agg_arn = _upsert_function(
        lc,
        LAMBDA_AGGREGATOR,
        agg_zip,
        {"STATE_TABLE": DYNAMO_STATE_TABLE, "KPIS_TABLE": DYNAMO_KPIS_TABLE},
        role_arn,
    )
    ingest_arn = _upsert_function(
        lc,
        LAMBDA_INGEST,
        ingest_zip,
        {"STATE_TABLE": DYNAMO_STATE_TABLE, "AGGREGATOR_FN": LAMBDA_AGGREGATOR},
        role_arn,
    )
    api_arn = _upsert_function(
        lc,
        LAMBDA_API,
        api_zip,
        {"STATE_TABLE": DYNAMO_STATE_TABLE, "KPIS_TABLE": DYNAMO_KPIS_TABLE},
        role_arn,
    )

    rule_arn = _ensure_topic_rule(iot, ingest_arn)
    _ensure_iot_invoke_permission(lc, LAMBDA_INGEST, rule_arn)

    state = load_state()
    state.update(
        {
            "lambdaIngestArn": ingest_arn,
            "lambdaAggregatorArn": agg_arn,
            "lambdaApiArn": api_arn,
            "topicRuleName": RULE_NAME,
            "topicRuleArn": rule_arn,
        }
    )
    save_state(state)
    log("03_setup_lambda completado")


if __name__ == "__main__":
    main()
