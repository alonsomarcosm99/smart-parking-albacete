"""Crea (o reutiliza) una API REST en API Gateway que expone la Lambda API.

Definicion de la API mediante OpenAPI v3 importado por boto3 (API Gateway v1).
La estrategia de integracion es AWS_PROXY: API Gateway pasa la peticion
completa a la Lambda y delega el formato de respuesta a la propia funcion.
"""
from __future__ import annotations

import json
import time

from botocore.exceptions import ClientError

from common import (
    API_NAME,
    AWS_REGION,
    LAMBDA_API,
    account_id,
    is_not_found,
    load_state,
    log,
    save_state,
    session,
)

STAGE = "prod"


def _openapi_spec(lambda_arn: str) -> dict:
    region = AWS_REGION
    uri = (
        f"arn:aws:apigateway:{region}:lambda:path/2015-03-31/functions/"
        f"{lambda_arn}/invocations"
    )
    integration = {
        "x-amazon-apigateway-integration": {
            "type": "aws_proxy",
            "httpMethod": "POST",
            "uri": uri,
            "payloadFormatVersion": "1.0",
            "passthroughBehavior": "when_no_match",
        }
    }
    operation = lambda summary: {
        "summary": summary,
        "responses": {"200": {"description": "OK"}},
        **integration,
    }
    return {
        "openapi": "3.0.1",
        "info": {"title": API_NAME, "version": "1.0"},
        "paths": {
            "/spots": {
                "get": operation("Lista de plazas (estado actual)"),
                "options": operation("CORS"),
            },
            "/spots/{spotId}": {
                "get": operation("Detalle de una plaza"),
                "options": operation("CORS"),
                "parameters": [
                    {"name": "spotId", "in": "path", "required": True, "schema": {"type": "string"}}
                ],
            },
            "/zones": {
                "get": operation("Lista de sub-zonas con KPIs agregados"),
                "options": operation("CORS"),
            },
            "/zones/{zoneId}/kpis": {
                "get": operation("Serie temporal de KPIs de una sub-zona"),
                "options": operation("CORS"),
                "parameters": [
                    {"name": "zoneId", "in": "path", "required": True, "schema": {"type": "string"}}
                ],
            },
        },
    }


def _find_api(client, name: str) -> str | None:
    apis = client.get_rest_apis(limit=500).get("items", [])
    for api in apis:
        if api.get("name") == name:
            return api["id"]
    return None


def _ensure_lambda_permission(lc, function_name: str, api_id: str) -> None:
    region = AWS_REGION
    acct = account_id()
    source_arn = f"arn:aws:execute-api:{region}:{acct}:{api_id}/*/*/*"
    statement_id = f"apigw-invoke-{function_name}-{api_id}"
    try:
        lc.add_permission(
            FunctionName=function_name,
            StatementId=statement_id,
            Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=source_arn,
        )
        log(f"Permiso de invocacion concedido a API Gateway sobre {function_name}")
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == "ResourceConflictException":
            return
        raise


def main() -> None:
    state = load_state()
    if "lambdaApiArn" not in state:
        raise RuntimeError("Falta lambdaApiArn en infra_state.json. Ejecuta 03_setup_lambda.py")

    apigw = session().client("apigateway")
    lc = session().client("lambda")

    spec = _openapi_spec(state["lambdaApiArn"])

    api_id = _find_api(apigw, API_NAME)
    if api_id:
        log(f"API REST {API_NAME} ya existe (id={api_id}), reimportando definicion")
        apigw.put_rest_api(
            restApiId=api_id,
            mode="overwrite",
            body=json.dumps(spec).encode("utf-8"),
        )
    else:
        resp = apigw.import_rest_api(body=json.dumps(spec).encode("utf-8"))
        api_id = resp["id"]
        log(f"API REST {API_NAME} creada (id={api_id})")

    _ensure_lambda_permission(lc, LAMBDA_API, api_id)

    apigw.create_deployment(restApiId=api_id, stageName=STAGE)
    log(f"Despliegue en stage {STAGE} realizado")

    base_url = f"https://{api_id}.execute-api.{AWS_REGION}.amazonaws.com/{STAGE}"
    state.update({"apiId": api_id, "apiBaseUrl": base_url, "apiStage": STAGE})
    save_state(state)
    log(f"API base URL: {base_url}")
    log("04_setup_api_gateway completado")


if __name__ == "__main__":
    main()
