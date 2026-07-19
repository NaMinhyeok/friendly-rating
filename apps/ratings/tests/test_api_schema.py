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
    "MediaForbiddenErrorEnvelope": {
        ("AUTHENTICATION", "AUTHENTICATION_REQUIRED"),
        ("AUTHENTICATION", "CSRF_FAILED"),
        ("AUTHORIZATION", "PARTICIPANT_REQUIRED"),
        ("AUTHORIZATION", "PERMISSION_DENIED"),
    },
    "NotFoundErrorEnvelope": {("NOT_FOUND", "NOT_FOUND")},
    "NotAcceptableErrorEnvelope": {("REQUEST", "NOT_ACCEPTABLE")},
    "ScoreOutOfRangeErrorEnvelope": {
        ("CONFLICT", "SCORE_OUT_OF_RANGE"),
        ("CONFLICT", "SCORE_UNCHANGED"),
        ("CONFLICT", "MEDIA_UPLOAD_CONFLICT"),
    },
    "MediaUploadConflictErrorEnvelope": {("CONFLICT", "MEDIA_UPLOAD_CONFLICT")},
    "RequestBodyTooLargeErrorEnvelope": {("REQUEST", "REQUEST_BODY_TOO_LARGE")},
    "UnsupportedMediaTypeErrorEnvelope": {("REQUEST", "UNSUPPORTED_MEDIA_TYPE")},
    "InternalServerErrorEnvelope": {("SERVER", "INTERNAL_SERVER_ERROR")},
    "MediaUploadsUnavailableErrorEnvelope": {("SERVER", "MEDIA_UPLOADS_UNAVAILABLE")},
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
    constant = resolved.get("const")
    if isinstance(constant, str):
        return [constant]
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
        "/api/v1/diary-entries/",
        "/api/v1/diary-entries/{diary_entry_id}/",
        "/api/v1/media-uploads/",
        "/api/v1/media-uploads/{upload_id}/complete/",
        "/api/v1/media-uploads/{upload_id}/discard/",
        "/api/v1/push-devices/register/",
        "/api/v1/push-devices/unregister/",
        "/api/v1/relationship-scores/",
        "/api/v1/score-changes/",
        "/api/v1/score-changes/{score_change_id}/",
        "/api/v1/score-changes/{score_change_id}/comments/",
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
    assert "required" not in request_schema
    assert request_schema["oneOf"] == [
        {"required": ["delta"]},
        {"required": ["targetScore"]},
    ]
    assert set(request_schema["properties"]) == {
        "delta",
        "targetScore",
        "reason",
        "mediaUploadIds",
    }
    delta_schema = request_schema["properties"]["delta"]
    assert delta_schema["type"] == "integer"
    assert delta_schema["maximum"] == 100
    assert delta_schema["minimum"] == -100
    assert delta_schema["not"] == {"const": 0}
    target_score_schema = request_schema["properties"]["targetScore"]
    assert target_score_schema["type"] == "integer"
    assert target_score_schema["minimum"] == 0
    assert target_score_schema["maximum"] == 100
    assert request_schema["properties"]["reason"]["type"] == "string"
    assert request_schema["properties"]["reason"]["maxLength"] == 200
    assert request_schema["properties"]["mediaUploadIds"] == {
        "type": "array",
        "items": {"type": "string", "format": "uuid"},
        "maxItems": 1,
        "uniqueItems": True,
        "default": [],
    }

    responses = operation["responses"]
    response_components = {
        "201": "ScoreChangeSuccessEnvelope",
        "400": "BadRequestErrorEnvelope",
        "403": "MediaForbiddenErrorEnvelope",
        "404": "NotFoundErrorEnvelope",
        "406": "NotAcceptableErrorEnvelope",
        "409": "ScoreOutOfRangeErrorEnvelope",
        "413": "RequestBodyTooLargeErrorEnvelope",
        "415": "UnsupportedMediaTypeErrorEnvelope",
        "500": "InternalServerErrorEnvelope",
        "503": "MediaUploadsUnavailableErrorEnvelope",
    }
    assert set(responses) == set(response_components)
    for status_code, component_name in response_components.items():
        assert responses[status_code]["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{component_name}"
        }

    assert document["components"]["securitySchemes"]["cookieAuth"] == {
        "type": "apiKey",
        "in": "cookie",
        "name": "sessionid",
    }


def test_media_upload_operations_declare_direct_upload_lifecycle_contract(client):
    document = client.get(
        reverse("api-schema"),
        HTTP_ACCEPT="application/json",
    ).json()
    paths = document["paths"]
    initiate = paths["/api/v1/media-uploads/"]["post"]
    complete = paths["/api/v1/media-uploads/{upload_id}/complete/"]["post"]
    discard = paths["/api/v1/media-uploads/{upload_id}/discard/"]["post"]

    for operation in (initiate, complete, discard):
        assert operation["security"] == [{"cookieAuth": []}]
        csrf = next(
            parameter
            for parameter in operation["parameters"]
            if parameter["name"] == "X-CSRFToken"
        )
        assert csrf["in"] == "header"
        assert csrf["required"] is True
        assert operation["requestBody"]["required"] is True
        assert set(operation["requestBody"]["content"]) == {"application/json"}

    initiate_request = _resolve_schema(
        document,
        initiate["requestBody"]["content"]["application/json"]["schema"],
    )
    assert initiate_request["type"] == "object"
    assert initiate_request["additionalProperties"] is False
    assert set(initiate_request["required"]) == {
        "purpose",
        "kind",
        "fileName",
        "contentType",
        "byteSize",
    }
    assert set(initiate_request["properties"]) == {
        "purpose",
        "kind",
        "fileName",
        "contentType",
        "byteSize",
        "scoreChangeId",
    }
    assert set(_enum_values(document, initiate_request["properties"]["purpose"])) == {
        "scoreChange",
        "comment",
        "diaryEntry",
    }
    assert set(_enum_values(document, initiate_request["properties"]["kind"])) == {
        "image",
        "video",
    }
    assert initiate_request["properties"]["fileName"] == {
        "type": "string",
        "minLength": 1,
        "maxLength": 255,
    }
    assert initiate_request["properties"]["contentType"] == {
        "type": "string",
        "minLength": 1,
        "maxLength": 100,
    }
    assert initiate_request["properties"]["byteSize"] == {
        "type": "integer",
        "minimum": 1,
    }
    assert initiate_request["properties"]["scoreChangeId"] == {
        "type": "integer",
        "minimum": 1,
    }
    assert initiate_request["allOf"] == [
        {
            "if": {"properties": {"purpose": {"const": "comment"}}},
            "then": {"required": ["scoreChangeId"]},
        },
        {
            "if": {"properties": {"purpose": {"const": "scoreChange"}}},
            "then": {
                "properties": {"kind": {"const": "image"}},
                "not": {"required": ["scoreChangeId"]},
            },
        },
        {
            "if": {"properties": {"purpose": {"const": "diaryEntry"}}},
            "then": {"not": {"required": ["scoreChangeId"]}},
        },
    ]

    for operation in (complete, discard):
        parameters = {
            parameter["name"]: parameter for parameter in operation["parameters"]
        }
        assert parameters["upload_id"] == {
            "in": "path",
            "name": "upload_id",
            "schema": {"type": "string", "format": "uuid"},
            "required": True,
        }
        request_schema = _resolve_schema(
            document,
            operation["requestBody"]["content"]["application/json"]["schema"],
        )
        assert request_schema == {
            "type": "object",
            "additionalProperties": False,
        }

    shared_error_responses = {
        "400": "BadRequestErrorEnvelope",
        "403": "MediaForbiddenErrorEnvelope",
        "406": "NotAcceptableErrorEnvelope",
        "409": "MediaUploadConflictErrorEnvelope",
        "413": "RequestBodyTooLargeErrorEnvelope",
        "415": "UnsupportedMediaTypeErrorEnvelope",
        "500": "InternalServerErrorEnvelope",
        "503": "MediaUploadsUnavailableErrorEnvelope",
    }
    operation_contracts = (
        (initiate, "201", "MediaUploadInitiatedSuccessEnvelope", True),
        (complete, "200", "CompletedMediaUploadSuccessEnvelope", True),
        (discard, "200", "MediaUploadDiscardedSuccessEnvelope", False),
    )
    for (
        operation,
        success_status,
        success_component,
        includes_not_found,
    ) in operation_contracts:
        response_components = {
            success_status: success_component,
            **shared_error_responses,
        }
        if includes_not_found:
            response_components["404"] = "NotFoundErrorEnvelope"
        assert set(operation["responses"]) == set(response_components)
        for status_code, component_name in response_components.items():
            assert operation["responses"][status_code]["content"]["application/json"][
                "schema"
            ] == {"$ref": f"#/components/schemas/{component_name}"}

    schemas = document["components"]["schemas"]
    initiated_envelope = schemas["MediaUploadInitiatedSuccessEnvelope"]
    assert initiated_envelope["properties"]["success"] == {
        "$ref": "#/components/schemas/InitiatedMediaUploadData"
    }
    initiated_data = schemas["InitiatedMediaUploadData"]
    initiated_fields = {"uploadId", "uploadUrl", "requiredHeaders", "expiresAt"}
    assert set(initiated_data["required"]) == initiated_fields
    assert set(initiated_data["properties"]) == initiated_fields
    assert initiated_data["properties"]["uploadId"]["format"] == "uuid"
    assert initiated_data["properties"]["uploadUrl"]["format"] == "uri"
    assert initiated_data["properties"]["requiredHeaders"]["additionalProperties"] == {
        "type": "string"
    }
    assert initiated_data["properties"]["expiresAt"]["format"] == "date-time"
    assert schemas["CompletedMediaUploadSuccessEnvelope"]["properties"]["success"] == {
        "$ref": "#/components/schemas/CompletedMediaUploadData"
    }
    assert _schema_types(
        document,
        schemas["MediaUploadDiscardedSuccessEnvelope"]["properties"]["success"],
    ) == {"null"}
    completed_data = schemas["CompletedMediaUploadData"]
    completed_fields = {"id", "kind", "fileName", "contentType", "byteSize"}
    assert set(completed_data["required"]) == completed_fields
    assert set(completed_data["properties"]) == completed_fields


def test_score_change_history_operation_declares_page_number_list_contract(client):
    document = client.get(
        reverse("api-schema"),
        HTTP_ACCEPT="application/json",
    ).json()
    operation = document["paths"]["/api/v1/score-changes/"]["get"]

    assert operation["security"] == [{"cookieAuth": []}]
    assert "requestBody" not in operation
    assert operation["parameters"] == [
        {
            "in": "query",
            "name": "pageNumber",
            "schema": {
                "type": "integer",
                "minimum": 1,
                "default": 1,
            },
        }
    ]
    response_components = {
        "200": "ScoreChangePageSuccessEnvelope",
        "400": "InvalidInputErrorEnvelope",
        "403": "ReadForbiddenErrorEnvelope",
        "404": "NotFoundErrorEnvelope",
        "406": "NotAcceptableErrorEnvelope",
        "500": "InternalServerErrorEnvelope",
    }
    assert set(operation["responses"]) == set(response_components)
    for status_code, component_name in response_components.items():
        assert operation["responses"][status_code]["content"]["application/json"][
            "schema"
        ] == {"$ref": f"#/components/schemas/{component_name}"}

    schemas = document["components"]["schemas"]
    envelope = schemas["ScoreChangePageSuccessEnvelope"]
    assert set(envelope["required"]) == {"resultType", "error", "success"}
    assert _enum_values(document, envelope["properties"]["resultType"]) == ["SUCCESS"]
    assert _schema_types(document, envelope["properties"]["error"]) == {"null"}
    assert envelope["properties"]["success"] == {
        "$ref": "#/components/schemas/ScoreChangePageData"
    }

    page_data = schemas["ScoreChangePageData"]
    assert set(page_data["required"]) == {"results", "paging"}
    assert set(page_data["properties"]) == {"results", "paging"}
    assert page_data["properties"]["results"] == {
        "type": "array",
        "maxItems": 20,
        "items": {"$ref": "#/components/schemas/ScoreChangeHistoryData"},
    }
    assert page_data["properties"]["paging"] == {
        "$ref": "#/components/schemas/PageNumberPaging"
    }

    item = schemas["ScoreChangeHistoryData"]
    item_fields = {
        "id",
        "sourceParticipant",
        "targetParticipant",
        "changedBy",
        "delta",
        "reason",
        "resultingScore",
        "createdAt",
        "commentCount",
        "threadUrl",
        "attachments",
    }
    assert set(item["required"]) == item_fields
    assert set(item["properties"]) == item_fields
    assert item["properties"]["id"]["minimum"] == 1
    assert item["properties"]["delta"]["minimum"] == -100
    assert item["properties"]["delta"]["maximum"] == 100
    assert item["properties"]["delta"]["not"] == {"const": 0}
    assert item["properties"]["reason"]["maxLength"] == 200
    assert item["properties"]["resultingScore"]["minimum"] == 0
    assert item["properties"]["resultingScore"]["maximum"] == 100
    assert item["properties"]["createdAt"]["format"] == "date-time"
    assert item["properties"]["commentCount"]["minimum"] == 0
    assert item["properties"]["threadUrl"]["type"] == "string"
    assert item["properties"]["attachments"] == {
        "type": "array",
        "items": {"$ref": "#/components/schemas/MediaAttachmentData"},
        "readOnly": True,
    }

    paging = schemas["PageNumberPaging"]
    assert set(paging["required"]) == {
        "pageNumber",
        "pageSize",
        "hasNext",
        "totalCount",
    }
    assert set(paging["properties"]) == set(paging["required"])
    assert paging["properties"]["pageNumber"]["minimum"] == 1
    assert paging["properties"]["pageSize"] == {
        "type": "integer",
        "const": 20,
    }
    assert paging["properties"]["hasNext"]["type"] == "boolean"
    assert paging["properties"]["totalCount"]["minimum"] == 0

    invalid_input = schemas["InvalidInputErrorEnvelope"]
    invalid_error = _error_variants(document, invalid_input)[0]
    assert _enum_values(document, invalid_error["properties"]["errorCode"]) == [
        "INVALID_INPUT"
    ]
    not_found = schemas["NotFoundErrorEnvelope"]
    not_found_error = _error_variants(document, not_found)[0]
    assert _enum_values(document, not_found_error["properties"]["errorType"]) == [
        "NOT_FOUND"
    ]
    assert _enum_values(document, not_found_error["properties"]["errorCode"]) == [
        "NOT_FOUND"
    ]


def test_score_change_thread_detail_declares_private_nested_comment_contract(client):
    document = client.get(
        reverse("api-schema"),
        HTTP_ACCEPT="application/json",
    ).json()
    operation = document["paths"]["/api/v1/score-changes/{score_change_id}/"]["get"]

    assert operation["security"] == [{"cookieAuth": []}]
    assert operation["parameters"] == [
        {
            "in": "path",
            "name": "score_change_id",
            "schema": {"type": "integer"},
            "required": True,
        }
    ]
    assert "requestBody" not in operation
    response_components = {
        "200": "ScoreChangeThreadSuccessEnvelope",
        "403": "ReadForbiddenErrorEnvelope",
        "404": "NotFoundErrorEnvelope",
        "406": "NotAcceptableErrorEnvelope",
        "500": "InternalServerErrorEnvelope",
    }
    assert set(operation["responses"]) == set(response_components)
    for status_code, component_name in response_components.items():
        assert operation["responses"][status_code]["content"]["application/json"][
            "schema"
        ] == {"$ref": f"#/components/schemas/{component_name}"}

    schemas = document["components"]["schemas"]
    envelope = schemas["ScoreChangeThreadSuccessEnvelope"]
    assert set(envelope["required"]) == {"resultType", "error", "success"}
    assert envelope["properties"]["success"] == {
        "$ref": "#/components/schemas/ScoreChangeThreadData"
    }
    thread = schemas["ScoreChangeThreadData"]
    assert set(thread["required"]) == {
        "id",
        "sourceParticipant",
        "targetParticipant",
        "changedBy",
        "delta",
        "reason",
        "resultingScore",
        "createdAt",
        "commentCount",
        "threadUrl",
        "attachments",
        "comments",
    }
    assert thread["properties"]["comments"] == {
        "type": "array",
        "items": {"$ref": "#/components/schemas/ScoreChangeCommentData"},
        "readOnly": True,
    }

    comment = schemas["ScoreChangeCommentData"]
    assert set(comment["required"]) == {
        "id",
        "author",
        "content",
        "createdAt",
        "isMine",
        "attachments",
    }
    assert comment["properties"]["content"]["maxLength"] == 500
    assert comment["properties"]["createdAt"]["format"] == "date-time"
    assert comment["properties"]["isMine"]["type"] == "boolean"
    assert comment["properties"]["attachments"] == {
        "type": "array",
        "items": {"$ref": "#/components/schemas/MediaAttachmentData"},
        "readOnly": True,
    }


def test_score_change_comment_operation_declares_strict_csrf_contract(client):
    document = client.get(
        reverse("api-schema"),
        HTTP_ACCEPT="application/json",
    ).json()
    operation = document["paths"]["/api/v1/score-changes/{score_change_id}/comments/"][
        "post"
    ]

    assert operation["security"] == [{"cookieAuth": []}]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    assert set(parameters) == {"X-CSRFToken", "score_change_id"}
    assert parameters["X-CSRFToken"]["in"] == "header"
    assert parameters["X-CSRFToken"]["required"] is True
    assert parameters["score_change_id"] == {
        "in": "path",
        "name": "score_change_id",
        "schema": {"type": "integer"},
        "required": True,
    }

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
            "content": {
                "type": "string",
                "maxLength": 500,
            },
            "mediaUploadIds": {
                "type": "array",
                "items": {"type": "string", "format": "uuid"},
                "maxItems": 4,
                "uniqueItems": True,
                "default": [],
            },
        },
        "anyOf": [
            {
                "required": ["content"],
                "properties": {"content": {"minLength": 1}},
            },
            {
                "required": ["mediaUploadIds"],
                "properties": {"mediaUploadIds": {"minItems": 1}},
            },
        ],
    }

    response_components = {
        "201": "ScoreChangeCommentSuccessEnvelope",
        "400": "BadRequestErrorEnvelope",
        "403": "MediaForbiddenErrorEnvelope",
        "404": "NotFoundErrorEnvelope",
        "406": "NotAcceptableErrorEnvelope",
        "409": "MediaUploadConflictErrorEnvelope",
        "413": "RequestBodyTooLargeErrorEnvelope",
        "415": "UnsupportedMediaTypeErrorEnvelope",
        "500": "InternalServerErrorEnvelope",
        "503": "MediaUploadsUnavailableErrorEnvelope",
    }
    assert set(operation["responses"]) == set(response_components)
    for status_code, component_name in response_components.items():
        assert operation["responses"][status_code]["content"]["application/json"][
            "schema"
        ] == {"$ref": f"#/components/schemas/{component_name}"}


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
        "attachments",
    }
    assert set(score_change["properties"]) == {
        "id",
        "delta",
        "reason",
        "resultingScore",
        "createdAt",
        "attachments",
    }
    assert "resulting_score" not in score_change["properties"]
    assert "created_at" not in score_change["properties"]
    assert score_change["properties"]["delta"]["minimum"] == -100
    assert score_change["properties"]["delta"]["maximum"] == 100
    assert score_change["properties"]["reason"]["maxLength"] == 200
    assert score_change["properties"]["resultingScore"]["minimum"] == 0
    assert score_change["properties"]["resultingScore"]["maximum"] == 100
    assert score_change["properties"]["attachments"] == {
        "type": "array",
        "items": {"$ref": "#/components/schemas/MediaAttachmentData"},
        "readOnly": True,
    }

    attachment = schemas["MediaAttachmentData"]
    attachment_fields = {
        "id",
        "kind",
        "fileName",
        "contentType",
        "byteSize",
        "contentUrl",
    }
    assert set(attachment["required"]) == attachment_fields
    assert set(attachment["properties"]) == attachment_fields
    assert attachment["properties"]["id"]["format"] == "uuid"
    assert attachment["properties"]["fileName"]["maxLength"] == 255
    assert attachment["properties"]["contentType"]["maxLength"] == 100
    assert attachment["properties"]["byteSize"]["minimum"] == 1
    assert attachment["properties"]["contentUrl"]["type"] == "string"

    observed_pairs_by_component = {}
    for component_name in ERROR_PAIRS:
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
