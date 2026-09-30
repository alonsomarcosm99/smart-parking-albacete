import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

from openapi_spec_validator import validate
import yaml


ROOT = Path(__file__).resolve().parents[2]


def load_lambda(monkeypatch, relative_path, name):
    monkeypatch.setenv("STATE_TABLE", "state")
    monkeypatch.setenv("KPIS_TABLE", "kpis")
    monkeypatch.setenv("AGGREGATOR_FN", "aggregator")
    state = Mock()
    kpis = Mock()
    resource = Mock()
    resource.Table.side_effect = lambda table_name: state if table_name == "state" else kpis
    lambda_client = Mock()
    monkeypatch.setattr("boto3.resource", lambda *args, **kwargs: resource)
    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: lambda_client)

    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module, state, kpis, lambda_client


def test_openapi_contract_is_valid():
    spec = yaml.safe_load((ROOT / "prototipo/api/openapi.yaml").read_text(encoding="utf-8"))
    validate(spec)
    assert {"/spots", "/spots/{spotId}", "/zones", "/zones/{zoneId}/kpis"}.issubset(
        spec["paths"]
    )


def test_ingest_rejects_incomplete_payload(monkeypatch):
    module, state, _, lambda_client = load_lambda(
        monkeypatch, "prototipo/lambdas/ingest/handler.py", "ingest_handler"
    )
    result = module.lambda_handler({"spotId": "ALB-Z1-001"}, None)
    assert result["ok"] is False
    state.put_item.assert_not_called()
    lambda_client.invoke.assert_not_called()


def test_ingest_is_idempotent_when_status_does_not_change(monkeypatch):
    module, state, _, lambda_client = load_lambda(
        monkeypatch, "prototipo/lambdas/ingest/handler.py", "ingest_handler_same"
    )
    state.get_item.return_value = {"Item": {"status": "free"}}
    event = {"spotId": "ALB-Z1-001", "zoneId": "Z1-CAMPUS", "status": "free"}
    result = module.lambda_handler(event, None)
    assert result == {"ok": True, "spotId": "ALB-Z1-001", "statusChanged": False}
    state.put_item.assert_called_once()
    lambda_client.invoke.assert_not_called()


def test_geojson_contract(monkeypatch):
    module, _, _, _ = load_lambda(
        monkeypatch, "prototipo/lambdas/api/handler.py", "api_handler"
    )
    payload = module._to_geojson(
        [{"spotId": "A", "zoneId": "Z1", "status": "free", "lat": 38.99, "lon": -1.86}]
    )
    assert payload["type"] == "FeatureCollection"
    assert payload["features"][0]["geometry"]["coordinates"] == [-1.86, 38.99]
    assert payload["features"][0]["properties"]["color"] == "#2ecc71"


def test_api_response_serializes_utf8_json(monkeypatch):
    module, _, _, _ = load_lambda(
        monkeypatch, "prototipo/lambdas/api/handler.py", "api_response_handler"
    )
    response = module._response({"message": "Albacete"})
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"message": "Albacete"}
    assert response["headers"]["Access-Control-Allow-Methods"] == "GET,OPTIONS"

