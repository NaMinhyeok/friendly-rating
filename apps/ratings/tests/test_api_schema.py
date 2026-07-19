from collections.abc import Mapping

from django.urls import reverse

ERROR_RESPONSE_COMPONENTS = {
    "400": "BadRequestErrorEnvelope",
    "403": "ForbiddenErrorEnvelope",
    "406": "NotAcceptableErrorEnvelope",
    "409": "ScoreOutOfRangeErrorEnvelope",
    "413": "RequestBodyTooLargeErrorEnvelope",
    "415": "UnsupportedMediaTypeErrorEnvelope",
    "500": "InternalServerErrorEnvelope",
}

PUSH_DEVICE_ERROR_RESPONSE_COMPONENTS = {
    status_code: component_name
    for status_code, component_name in ERROR_RESPONSE_COMPONENTS.items()
    if status_code != "409"
}

ERROR_PAIRS = {
    "BadRequestErrorEnvelope": {
        ("REQUEST", "INVALID_JSON"),
        ("VALIDATION", "INVALID_INPUT"),
    },
    "ForbiddenErrorEnvelope": {
        ("AUTHENTICATION", "AUTHENTICATION_REQUIRED"),
        ("AUTHENTICATION", "CSRF_FAILED"),
        ("AUTHORIZATION", "PARTICIPANT_REQUIRED"),
    },
    "NotAcceptableErrorEnvelope": {("REQUEST", "NOT_ACCEPTABLE")},
    "ScoreOutOfRangeErrorEnvelope": {("CONFLICT", "SCORE_OUT_OF_RANGE")},
    "RequestBodyTooLargeErrorEnvelope": {("REQUEST", "REQUEST_BODY_TOO_LARGE")},
    "UnsupportedMediaTypeErrorEnvelope": {("REQUEST", "UNSUPPORTED_MEDIA_TYPE")},
    "InternalServerErrorEnvelope": {("SERVER", "INTERNAL_SERVER_ERROR")},
}


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


def _error_variants(document, envelope_schema) -> list[Mapping]:
    envelope = _resolve_schema(document, envelope_schema)
    error_schema = _resolve_schema(document, envelope["properties"]["error"])
    members = error_schema.get("oneOf")
    if members is None:
        return [error_schema]
    assert isinstance(members, list)
    return [_resolve_schema(document, member) for member in members]


def test_openapi_schema_is_public_standard_oas_31_document(client):
    assert reverse("api-schema") == "/api/schema/"

    response = client.get(reverse("api-schema"), HTTP_ACCEPT="application/json")

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/json"
    document = response.json()
    assert document["openapi"] == "3.1.0"
    assert document["info"]["title"] == "우리 사이 API"
    assert "resultType" not in document
    assert set(document["paths"]) == {
        "/api/v1/push-devices/register/",
        "/api/v1/push-devices/unregister/",
        "/api/v1/relationship-scores/",
        "/api/v1/score-changes/",
    }


def test_relationship_score_operation_declares_read_only_list_contract(client):
    document = client.get(
        reverse("api-schema"),
        HTTP_ACCEPT="application/json",
    ).json()
    operation = document["paths"]["/api/v1/relationship-scores/"]["get"]

    assert operation["security"] == [{"cookieAuth": []}]
    assert "parameters" not in operation
    assert "requestBody" not in operation
    assert set(operation["responses"]) == {"200", "403", "406", "500"}
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/RelationshipScoreListSuccessEnvelope"
    }
    assert operation["responses"]["403"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ReadForbiddenErrorEnvelope"
    }

    schemas = document["components"]["schemas"]
    envelope = schemas["RelationshipScoreListSuccessEnvelope"]
    assert set(envelope["required"]) == {"resultType", "error", "success"}
    assert _enum_values(document, envelope["properties"]["resultType"]) == ["SUCCESS"]
    assert _schema_types(document, envelope["properties"]["error"]) == {"null"}

    list_data = schemas["RelationshipScoreListData"]
    assert list_data["required"] == ["results"]
    assert set(list_data["properties"]) == {"results"}
    results = list_data["properties"]["results"]
    assert results["type"] == "array"
    assert results["items"] == {"$ref": "#/components/schemas/RelationshipScoreData"}

    score = schemas["RelationshipScoreData"]
    assert set(score["required"]) == {
        "sourceParticipant",
        "targetParticipant",
        "currentScore",
        "updatedAt",
        "isMine",
    }
    assert set(score["properties"]) == {
        "sourceParticipant",
        "targetParticipant",
        "currentScore",
        "updatedAt",
        "isMine",
    }
    assert score["properties"]["currentScore"]["minimum"] == 0
    assert score["properties"]["currentScore"]["maximum"] == 100
    assert score["properties"]["updatedAt"]["format"] == "date-time"
    assert score["properties"]["isMine"]["type"] == "boolean"

    participant = schemas["ParticipantSummary"]
    assert set(participant["required"]) == {"slot", "displayName"}
    assert set(participant["properties"]) == {"slot", "displayName"}
    assert participant["properties"]["slot"]["minimum"] == 1
    assert participant["properties"]["slot"]["maximum"] == 2
    assert participant["properties"]["displayName"]["maxLength"] == 30

    forbidden = schemas["ReadForbiddenErrorEnvelope"]
    observed_pairs = {
        (
            _enum_values(document, variant["properties"]["errorType"])[0],
            _enum_values(document, variant["properties"]["errorCode"])[0],
        )
        for variant in _error_variants(document, forbidden)
    }
    assert observed_pairs == {
        ("AUTHENTICATION", "AUTHENTICATION_REQUIRED"),
        ("AUTHORIZATION", "PARTICIPANT_REQUIRED"),
    }


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
    for status_code, component_name in ERROR_RESPONSE_COMPONENTS.items():
        assert responses[status_code]["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{component_name}"
        }

    assert document["components"]["securitySchemes"]["cookieAuth"] == {
        "type": "apiKey",
        "in": "cookie",
        "name": "sessionid",
    }


def test_push_device_operations_declare_strict_request_and_envelope_contract(client):
    document = client.get(
        reverse("api-schema"),
        HTTP_ACCEPT="application/json",
    ).json()

    operation_contracts = {
        "/api/v1/push-devices/register/": (
            "PushDeviceRegisteredSuccessEnvelope",
            "PushDeviceRegisteredData",
            True,
        ),
        "/api/v1/push-devices/unregister/": (
            "PushDeviceUnregisteredSuccessEnvelope",
            "PushDeviceUnregisteredData",
            False,
        ),
    }
    for path, (
        success_component_name,
        data_component_name,
        registered_value,
    ) in operation_contracts.items():
        operation = document["paths"][path]["post"]
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
        assert request_schema == {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "fid": {
                    "type": "string",
                    "minLength": 22,
                    "maxLength": 22,
                    "pattern": "^[cdef][A-Za-z0-9_-]{21}$",
                },
            },
            "required": ["fid"],
        }

        responses = operation["responses"]
        assert set(responses) == {
            "200",
            *PUSH_DEVICE_ERROR_RESPONSE_COMPONENTS,
        }
        assert responses["200"]["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{success_component_name}"
        }
        for (
            status_code,
            component_name,
        ) in PUSH_DEVICE_ERROR_RESPONSE_COMPONENTS.items():
            assert responses[status_code]["content"]["application/json"]["schema"] == {
                "$ref": f"#/components/schemas/{component_name}"
            }

        success_envelope = document["components"]["schemas"][success_component_name]
        assert set(success_envelope["required"]) == {
            "resultType",
            "error",
            "success",
        }
        assert _enum_values(
            document,
            success_envelope["properties"]["resultType"],
        ) == ["SUCCESS"]
        assert _schema_types(
            document,
            success_envelope["properties"]["error"],
        ) == {"null"}
        assert success_envelope["properties"]["success"] == {
            "$ref": f"#/components/schemas/{data_component_name}"
        }

        data_schema = document["components"]["schemas"][data_component_name]
        assert data_schema["required"] == ["registered"]
        assert set(data_schema["properties"]) == {"registered"}
        assert data_schema["properties"]["registered"]["type"] == "boolean"
        assert data_schema["properties"]["registered"]["const"] is registered_value


def test_openapi_envelopes_are_exclusive_and_fields_match_runtime_contract(client):
    document = client.get(
        reverse("api-schema"),
        HTTP_ACCEPT="application/json",
    ).json()
    schemas = document["components"]["schemas"]
    success_envelope = schemas["ScoreChangeSuccessEnvelope"]

    assert set(success_envelope["required"]) == {"resultType", "error", "success"}
    assert _enum_values(
        document,
        success_envelope["properties"]["resultType"],
    ) == ["SUCCESS"]
    assert _schema_types(document, success_envelope["properties"]["error"]) == {"null"}
    assert success_envelope["properties"]["success"] == {
        "$ref": "#/components/schemas/ScoreChangeData"
    }

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

    observed_pairs_by_component = {}
    for component_name in ERROR_RESPONSE_COMPONENTS.values():
        envelope = schemas[component_name]
        assert set(envelope["required"]) == {"resultType", "error", "success"}
        assert _enum_values(
            document,
            envelope["properties"]["resultType"],
        ) == ["ERROR"]
        assert _schema_types(document, envelope["properties"]["success"]) == {"null"}

        observed_pairs = set()
        for api_error in _error_variants(document, envelope):
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
            error_type = _enum_values(
                document,
                api_error["properties"]["errorType"],
            )
            error_code = _enum_values(
                document,
                api_error["properties"]["errorCode"],
            )
            assert len(error_type) == 1
            assert len(error_code) == 1
            observed_pairs.add((error_type[0], error_code[0]))

            details = api_error["properties"]["details"]
            assert details["type"] == "array"
            if error_code == ["INVALID_INPUT"]:
                assert details["minItems"] == 1
            else:
                assert details["maxItems"] == 0

        observed_pairs_by_component[component_name] = observed_pairs

    assert observed_pairs_by_component == ERROR_PAIRS

    detail_schema = schemas["ErrorDetail"]
    assert set(detail_schema["required"]) == {"field", "code", "message"}
    assert set(detail_schema["properties"]) == {"field", "code", "message"}
    assert _schema_types(document, detail_schema["properties"]["field"]) == {
        "string",
        "null",
    }
