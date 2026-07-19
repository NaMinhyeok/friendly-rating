from collections.abc import Mapping

from django.urls import reverse


def _resolve_schema(document, schema):
    resolved = schema
    while isinstance(resolved, Mapping) and "$ref" in resolved:
        reference = resolved["$ref"]
        assert isinstance(reference, str)
        assert reference.startswith("#/")
        resolved = document
        for path_part in reference.removeprefix("#/").split("/"):
            resolved = resolved[path_part]
    assert isinstance(resolved, Mapping)
    return resolved


def _schema_types(document, schema) -> set[str]:
    resolved = _resolve_schema(document, schema)
    types: set[str] = set()
    declared_type = resolved.get("type")
    if isinstance(declared_type, str):
        types.add(declared_type)
    elif isinstance(declared_type, list):
        types.update(declared_type)
    for union_key in ("oneOf", "anyOf"):
        for member in resolved.get(union_key, []):
            types.update(_schema_types(document, member))
    if resolved.get("nullable") is True:
        if not types:
            types.add("any")
        types.add("null")
    return types


def _enum_values(document, schema) -> list[str]:
    resolved = _resolve_schema(document, schema)
    values = resolved.get("enum")
    assert isinstance(values, list)
    return values


def test_openapi_schema_is_public_standard_oas_31_document(client):
    assert reverse("api-schema") == "/api/schema/"

    response = client.get(reverse("api-schema"), HTTP_ACCEPT="application/json")

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/json"
    document = response.json()
    assert document["openapi"] == "3.1.0"
    assert document["info"]["title"] == "우리 사이 API"
    assert "resultType" not in document
    assert set(document["paths"]) == {"/api/v1/score-changes/"}


def test_score_change_operation_declares_session_csrf_json_and_status_contract(client):
    document = client.get(
        reverse("api-schema"),
        HTTP_ACCEPT="application/json",
    ).json()
    operation = document["paths"]["/api/v1/score-changes/"]["post"]

    assert operation["security"] == [{"cookieAuth": []}]
    csrf_parameter = next(
        parameter
        for parameter in operation["parameters"]
        if parameter["name"] == "X-CSRFToken"
    )
    assert csrf_parameter["in"] == "header"
    assert csrf_parameter["required"] is True

    request_body = operation["requestBody"]
    assert request_body["required"] is True
    assert set(request_body["content"]) == {"application/json"}
    request_schema = _resolve_schema(
        document,
        request_body["content"]["application/json"]["schema"],
    )
    assert request_schema["type"] == "object"
    assert request_schema["additionalProperties"] is False
    assert request_schema["required"] == ["delta"]
    assert set(request_schema["properties"]) == {"delta", "reason"}
    delta_schema = request_schema["properties"]["delta"]
    assert delta_schema["type"] == "integer"
    assert delta_schema["maximum"] == 100
    assert delta_schema["minimum"] == -100
    assert delta_schema["not"] == {"const": 0}
    assert request_schema["properties"]["reason"]["type"] == "string"
    assert request_schema["properties"]["reason"]["maxLength"] == 200

    responses = operation["responses"]
    assert set(responses) == {
        "201",
        "400",
        "403",
        "405",
        "406",
        "409",
        "413",
        "415",
        "500",
    }
    success_response_schema = responses["201"]["content"]["application/json"]["schema"]
    assert success_response_schema == {
        "$ref": "#/components/schemas/ScoreChangeSuccessEnvelope"
    }
    for status_code in ("400", "403", "405", "406", "409", "413", "415", "500"):
        assert responses[status_code]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorEnvelope"
        }

    assert document["components"]["securitySchemes"]["cookieAuth"] == {
        "type": "apiKey",
        "in": "cookie",
        "name": "sessionid",
    }


def test_openapi_envelopes_are_exclusive_and_fields_match_runtime_contract(client):
    document = client.get(
        reverse("api-schema"),
        HTTP_ACCEPT="application/json",
    ).json()
    schemas = document["components"]["schemas"]
    success_envelope = schemas["ScoreChangeSuccessEnvelope"]
    error_envelope = schemas["ErrorEnvelope"]

    assert set(success_envelope["required"]) == {"resultType", "error", "success"}
    assert _enum_values(
        document,
        success_envelope["properties"]["resultType"],
    ) == ["SUCCESS"]
    assert _schema_types(document, success_envelope["properties"]["error"]) == {"null"}
    assert success_envelope["properties"]["success"] == {
        "$ref": "#/components/schemas/ScoreChangeData"
    }

    assert set(error_envelope["required"]) == {"resultType", "error", "success"}
    assert _enum_values(
        document,
        error_envelope["properties"]["resultType"],
    ) == ["ERROR"]
    assert error_envelope["properties"]["error"] == {
        "$ref": "#/components/schemas/ApiError"
    }
    assert _schema_types(document, error_envelope["properties"]["success"]) == {"null"}

    score_change = schemas["ScoreChangeData"]
    assert set(score_change["required"]) == {
        "id",
        "delta",
        "reason",
        "resultingScore",
        "createdAt",
    }
    assert set(score_change["properties"]) == {
        "id",
        "delta",
        "reason",
        "resultingScore",
        "createdAt",
    }
    assert "resulting_score" not in score_change["properties"]
    assert "created_at" not in score_change["properties"]
    assert score_change["properties"]["delta"]["minimum"] == -100
    assert score_change["properties"]["delta"]["maximum"] == 100
    assert score_change["properties"]["reason"]["maxLength"] == 200
    assert score_change["properties"]["resultingScore"]["minimum"] == 0
    assert score_change["properties"]["resultingScore"]["maximum"] == 100

    api_error = schemas["ApiError"]
    assert set(api_error["required"]) == {
        "errorType",
        "errorCode",
        "reason",
        "details",
    }
    assert set(api_error["properties"]) == {
        "errorType",
        "errorCode",
        "reason",
        "details",
    }
    assert set(_enum_values(document, api_error["properties"]["errorType"])) == {
        "AUTHENTICATION",
        "AUTHORIZATION",
        "VALIDATION",
        "REQUEST",
        "NOT_FOUND",
        "CONFLICT",
        "RATE_LIMIT",
        "SERVER",
    }
    assert set(_enum_values(document, api_error["properties"]["errorCode"])) == {
        "REQUEST_FAILED",
        "INVALID_JSON",
        "INVALID_INPUT",
        "REQUEST_BODY_TOO_LARGE",
        "UNSUPPORTED_MEDIA_TYPE",
        "METHOD_NOT_ALLOWED",
        "NOT_ACCEPTABLE",
        "AUTHENTICATION_REQUIRED",
        "AUTHENTICATION_FAILED",
        "CSRF_FAILED",
        "PERMISSION_DENIED",
        "PARTICIPANT_REQUIRED",
        "NOT_FOUND",
        "SCORE_OUT_OF_RANGE",
        "RATE_LIMITED",
        "INTERNAL_SERVER_ERROR",
    }
    details = api_error["properties"]["details"]
    assert details["type"] == "array"
    detail_schema = _resolve_schema(document, details["items"])
    assert set(detail_schema["required"]) == {"field", "code", "message"}
    assert set(detail_schema["properties"]) == {"field", "code", "message"}
    assert _schema_types(document, detail_schema["properties"]["field"]) == {
        "string",
        "null",
    }
