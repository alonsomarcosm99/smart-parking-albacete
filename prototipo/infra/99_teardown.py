"""Elimina todos los recursos creados por los scripts 01..04.

Pensado para ejecutarse al cerrar la sesion del AWS Academy Learner Lab,
de forma que la cuenta vuelva al estado inicial y no queden artefactos. Es
robusto a fallos: ignora ResourceNotFound y continua con el siguiente recurso.
"""
from __future__ import annotations

import time

from botocore.exceptions import ClientError

from common import (
    AWS_REGION,
    DYNAMO_KPIS_TABLE,
    DYNAMO_STATE_TABLE,
    LAMBDA_AGGREGATOR,
    LAMBDA_API,
    LAMBDA_INGEST,
    POLICY_NAME,
    RULE_NAME,
    THING_GROUP_NAME,
    THING_TYPE_NAME,
    is_not_found,
    load_state,
    log,
    save_state,
    session,
)


def _safe(label: str, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
        log(f"OK  -> {label}")
    except ClientError as err:
        if is_not_found(err):
            log(f"--- {label} (no existe)")
        else:
            log(f"ERR {label}: {err}")
    except Exception as err:  # pragma: no cover
        log(f"ERR {label}: {err}")


def main() -> None:
    state = load_state()

    iot = session().client("iot")
    lc = session().client("lambda")
    ddb = session().client("dynamodb")
    apigw = session().client("apigateway")

    # 1. API Gateway
    if state.get("apiId"):
        _safe(f"API Gateway {state['apiId']}", apigw.delete_rest_api, restApiId=state["apiId"])

    # 2. Lambdas
    for fn in (LAMBDA_API, LAMBDA_INGEST, LAMBDA_AGGREGATOR):
        _safe(f"Lambda {fn}", lc.delete_function, FunctionName=fn)

    # 3. Topic Rule
    _safe(f"IoT Topic Rule {RULE_NAME}", iot.delete_topic_rule, ruleName=RULE_NAME)

    # 4. Things + cert + policy
    cert_id = state.get("certificateId")
    cert_arn = state.get("certificateArn")
    things = state.get("things", [])
    for thing in things:
        if cert_arn:
            _safe(
                f"Detach cert de {thing}",
                iot.detach_thing_principal,
                thingName=thing,
                principal=cert_arn,
            )
        _safe(f"Delete thing {thing}", iot.delete_thing, thingName=thing)

    if cert_arn:
        _safe("Detach policy del cert", iot.detach_policy, policyName=POLICY_NAME, target=cert_arn)
    _safe(f"Delete policy {POLICY_NAME}", iot.delete_policy, policyName=POLICY_NAME)

    if cert_id:
        _safe(
            "Desactivar certificado",
            iot.update_certificate,
            certificateId=cert_id,
            newStatus="INACTIVE",
        )
        _safe("Borrar certificado", iot.delete_certificate, certificateId=cert_id, forceDelete=True)

    _safe(
        f"Delete thing group {THING_GROUP_NAME}",
        iot.delete_thing_group,
        thingGroupName=THING_GROUP_NAME,
    )
    _safe(
        f"Deprecate thing type {THING_TYPE_NAME}",
        iot.deprecate_thing_type,
        thingTypeName=THING_TYPE_NAME,
    )
    # AWS exige esperar 5 minutos tras deprecate antes de delete; lo intentamos igualmente
    _safe(
        f"Delete thing type {THING_TYPE_NAME}",
        iot.delete_thing_type,
        thingTypeName=THING_TYPE_NAME,
    )

    # 5. DynamoDB
    for tname in (DYNAMO_STATE_TABLE, DYNAMO_KPIS_TABLE):
        _safe(f"Delete table {tname}", ddb.delete_table, TableName=tname)

    save_state({})
    log("99_teardown completado")


if __name__ == "__main__":
    main()
