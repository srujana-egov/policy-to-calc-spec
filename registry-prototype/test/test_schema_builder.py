"""Tests: SchemaBuilder + validate_schema_request, mirroring
../workflow-prototype/test_workflow_builder.py's role for the registry side -- the real
license-registry example (matching the actual registry service's own Postman collection payload)
built entirely through builder calls, plus one test per completeness check.

Also replays three more real schemas found by scraping the entire digitnxt GitHub org (not just
digit3) for registry examples: `pgr2` (a real tutorial schema -- nested objects, pattern,
minimum/maximum), `trade-license` (digit-specs' own canonical example -- nested object with a
required sub-field, two unique constraints), and `PGR.ServiceCategory` (a real schema code
containing a dot, which the original schema-code regex would have rejected).
"""


import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from pathlib import Path

from builder import SchemaBuilder, camel_field_name
from models import IndexDef, PropertyDef, SchemaDefinition, SchemaRequest
from validate import validate_schema_request

FIXTURES = Path(__file__).parent.parent / "fixtures"
PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name} FAILED: {detail}"
    PASSED.append(name)


def load_real_world(name: str) -> dict:
    return json.loads((FIXTURES / "real_world" / name).read_text())


def canonicalize_real_world(real: dict) -> dict:
    """Strips fields this prototype deliberately doesn't model, so a real-world fixture can be
    compared against what we actually build: `additionalProperties` (this prototype always
    forces it to `false` for safety; some real examples simply omit it, which JSON Schema treats
    as `true` -- a deliberate difference, not an oversight), `x-ref-schema` (cross-schema
    references, out of scope, same reasoning as EscalationConfig for the workflow wizard), and
    `version`/`isActive` (server-assigned response fields, never part of a create request)."""
    result = json.loads(json.dumps(real))
    result["definition"].pop("additionalProperties", None)
    result.pop("x-ref-schema", None)
    result.pop("version", None)
    result.pop("isActive", None)
    result.pop("_comment_x_indexes_moved", None)
    return result


def canonicalize_built(schema: SchemaRequest) -> dict:
    result = json.loads(schema.model_dump_json(by_alias=True, exclude_none=True))
    result["definition"].pop("additionalProperties", None)
    return result


def build_license_registry() -> SchemaBuilder:
    b = SchemaBuilder("license-registry")
    b.add_field("License Number", "string", required=True, description="Unique license identifier")
    b.add_field("Holder Name", "string", required=True)
    b.add_field("Issue Date", "string", required=True, format="date")
    b.add_field("Status", "string", required=True, enum=["ACTIVE", "SUSPENDED", "REVOKED"])
    b.add_unique_constraint(["licenseNumber"])
    b.add_index("status")
    return b


def test_01_license_registry_builds_and_validates():
    schema = build_license_registry().build()
    errors = validate_schema_request(schema)
    check("01-builds-clean", not errors, errors)
    check("01-four-fields",
          set(schema.definition.properties) == {"licenseNumber", "holderName", "issueDate", "status"})
    check("01-all-required",
          set(schema.definition.required) == {"licenseNumber", "holderName", "issueDate", "status"})
    check("01-status-enum", schema.definition.properties["status"].enum == ["ACTIVE", "SUSPENDED", "REVOKED"])
    check("01-issue-date-format", schema.definition.properties["issueDate"].format == "date")
    check("01-unique-on-license-number", schema.x_unique == [["licenseNumber"]])
    check("01-index-on-status", schema.x_indexes[0].fieldPath == "status")


def test_02_camel_field_name():
    check("02-simple", camel_field_name("License Number") == "licenseNumber")
    check("02-already-camel", camel_field_name("holderName") == "holderName")
    check("02-punctuation", camel_field_name("a b_c-d") == "aBCD")
    check("02-empty-falls-back", camel_field_name("!!!") == "field")


def test_03_empty_schema_code_caught():
    b = SchemaBuilder("")
    b.add_field("X", "string")
    errors = validate_schema_request(b.build())
    check("03-empty-code-caught", any("schemaCode is empty" in e for e in errors), errors)


def test_04_bad_schema_code_format_caught():
    b = SchemaBuilder("has spaces")
    b.add_field("X", "string")
    errors = validate_schema_request(b.build())
    check("04-bad-format-caught", any("should start with a letter" in e for e in errors), errors)


def test_05_no_fields_caught():
    errors = validate_schema_request(SchemaBuilder("x").build())
    check("05-no-fields-caught", any("no fields defined" in e for e in errors), errors)


def test_06_dangling_required_caught():
    schema = SchemaRequest(schemaCode="x", definition=SchemaDefinition(
        properties={"a": PropertyDef(type="string")}, required=["a", "ghost"]))
    errors = validate_schema_request(schema)
    check("06-dangling-required-caught", any("'ghost' is listed as required" in e for e in errors), errors)


def test_07_dangling_unique_constraint_caught():
    schema = SchemaRequest(schemaCode="x", definition=SchemaDefinition(
        properties={"a": PropertyDef(type="string")}), **{"x-unique": [["a", "ghost"]]})
    errors = validate_schema_request(schema)
    check("07-dangling-unique-caught", any("unique constraint references 'ghost'" in e for e in errors), errors)


def test_08_dangling_index_caught():
    schema = SchemaRequest(schemaCode="x", definition=SchemaDefinition(
        properties={"a": PropertyDef(type="string")}), **{"x-indexes": [IndexDef(fieldPath="ghost")]})
    errors = validate_schema_request(schema)
    check("08-dangling-index-caught", any("index references 'ghost'" in e for e in errors), errors)


def test_09_enum_on_wrong_type_caught():
    schema = SchemaRequest(schemaCode="x", definition=SchemaDefinition(
        properties={"a": PropertyDef(type="boolean", enum=["true", "false"])}))
    errors = validate_schema_request(schema)
    check("09-enum-on-boolean-caught", any("has an enum but type 'boolean'" in e for e in errors), errors)


def test_10_remove_field_also_removes_from_required():
    b = build_license_registry()
    b.remove_field("holderName")
    schema = b.build()
    check("10-field-removed", "holderName" not in schema.definition.properties)
    check("10-required-cleaned", "holderName" not in schema.definition.required)


def test_11_no_false_positives():
    errors = validate_schema_request(build_license_registry().build())
    check("11-no-false-positives", not errors, errors)


# ---------------------------------------------------------------------------
# Real schemas scraped from across the digitnxt org (fixtures/real_world/)
# ---------------------------------------------------------------------------

def build_pgr_service_category() -> SchemaBuilder:
    """examples/pgr/pgr-schemas/pgr-service-category-schema.yaml -- schemaCode contains a dot."""
    b = SchemaBuilder("PGR.ServiceCategory")
    b.add_field("code", "string", required=True)
    b.add_field("name", "string", required=True)
    b.add_field("active", "boolean")
    return b


def test_12_pgr_service_category_matches_real_schema():
    schema = build_pgr_service_category().build()
    check("12-validates-clean", not validate_schema_request(schema))
    real = canonicalize_real_world(load_real_world("pgr_service_category.json"))
    check("12-matches-real-schema", canonicalize_built(schema) == real, canonicalize_built(schema))


def build_trade_license() -> SchemaBuilder:
    """digit-specs/v3.0.0/registry.yaml's own canonical example -- a nested object (`address`)
    with a required sub-field, and two unique constraints (one single-field, one compound)."""
    b = SchemaBuilder("trade-license")
    b.add_field("applicantId", "string", required=True)
    b.add_field("businessName", "string", required=True)
    b.add_field("tradeType", "string", required=True)
    address = b.add_field("address", "object")
    b.add_nested_field(address, "city", "string", required=True)
    b.add_nested_field(address, "pincode", "string")
    b.add_unique_constraint(["businessName"])
    b.add_unique_constraint(["applicantId", "tradeType"])
    b.add_index("tradeType", name="idx_trade_type")
    return b


def test_13_trade_license_matches_real_schema():
    schema = build_trade_license().build()
    check("13-validates-clean", not validate_schema_request(schema))
    real = canonicalize_real_world(load_real_world("trade_license.json"))
    check("13-matches-real-schema", canonicalize_built(schema) == real, canonicalize_built(schema))


def build_pgr2() -> SchemaBuilder:
    """docs/tutorials/backend/pgr2-registry-schema.yaml -- a real tutorial schema using pattern
    (10-digit mobile, 6-digit pincode), minimum/maximum (lat/long bounds), and nested objects.
    `address` nests a full second level (`address.auditDetails`, itself with its own sub-fields)
    -- deeper than the wizard's own one-level-only UX limit (see wizard.py), so that specific
    sub-object is constructed directly via PropertyDef rather than through
    SchemaBuilder.add_nested_field(), which only supports one level by design. The model itself
    has no depth restriction; only the interactive wizard does."""
    b = SchemaBuilder("pgr2")
    plain_fields = [
        ("serviceRequestId", "Unique identifier for the service request"),
        ("tenantId", "Tenant identifier"),
        ("serviceCode", "Code identifying the type of service"),
        ("description", "Description of the service request"),
        ("accountId", "Account identifier of the requester"),
        ("source", "Source of the service request"),
        ("applicationStatus", "Current status of the application"),
        ("action", "Action to be performed"),
        ("fileStoreId", "File store identifier for attachments"),
        ("boundaryCode", "Boundary/ward code where service is requested"),
        ("individualId", "Individual identifier of the requester"),
    ]
    for name, desc in plain_fields:
        b.add_field(name, "string", required=(name in ("serviceRequestId", "tenantId")), description=desc)
    b.add_field("email", "string", format="email", description="Email address of the requester")
    b.add_field("mobile", "string", pattern="^[0-9]{10}$", description="Mobile number of the requester")
    b.add_field("processId", "string", description="Process identifier for workflow")
    b.add_field("workflowInstanceId", "string", description="Workflow instance identifier")

    audit = b.add_field("auditDetails", "object", description="Audit information for the record")
    for name, type_, format_ in [("createdBy", "string", None), ("createdTime", "integer", "int64"),
                                  ("lastModifiedBy", "string", None), ("lastModifiedTime", "integer", "int64")]:
        b.add_nested_field(audit, name, type_, format=format_)

    address = b.add_field("address", "object", description="Address information for the service request")
    for name, type_, extra in [
        ("id", "string", {}), ("serviceRequestId", "string", {}), ("address", "string", {}),
        ("city", "string", {}), ("pincode", "string", {"pattern": "^[0-9]{6}$"}),
        ("latitude", "number", {"format": "double", "minimum": -90, "maximum": 90}),
        ("longitude", "number", {"format": "double", "minimum": -180, "maximum": 180}),
    ]:
        b.add_nested_field(address, name, type_, **extra)

    b.properties[address].properties["auditDetails"] = PropertyDef(
        type="object",
        properties={
            "createdBy": PropertyDef(type="string"),
            "createdTime": PropertyDef(type="integer", format="int64"),
            "lastModifiedBy": PropertyDef(type="string"),
            "lastModifiedTime": PropertyDef(type="integer", format="int64"),
        },
    )

    b.add_index("serviceRequestId", name="idx_pgr2_service_request_id")
    b.add_index("tenantId", name="idx_pgr2_tenant_id")
    b.add_index("applicationStatus", name="idx_pgr2_application_status")
    b.add_index("boundaryCode", method="gin")
    return b


def test_14_pgr2_matches_real_schema():
    schema = build_pgr2().build()
    check("14-validates-clean", not validate_schema_request(schema))
    real = canonicalize_real_world(load_real_world("pgr2.json"))
    check("14-matches-real-schema", canonicalize_built(schema) == real, canonicalize_built(schema))
    check("14-two-level-nesting-preserved",
          schema.definition.properties["address"].properties["auditDetails"].properties["createdBy"].type == "string")


# ---------------------------------------------------------------------------
# New completeness checks (pattern/minimum/maximum/nested sub-fields)
# ---------------------------------------------------------------------------

def test_15_dotted_schema_code_is_valid():
    """The original regex (no dots) would have rejected examples/pgr/pgr-schemas/
    pgr-service-category-schema.yaml's own real schemaCode."""
    errors = validate_schema_request(build_pgr_service_category().build())
    check("15-dotted-code-accepted", not any("schemaCode" in e for e in errors), errors)


def test_16_pattern_on_non_string_caught():
    schema = SchemaRequest(schemaCode="x", definition=SchemaDefinition(
        properties={"a": PropertyDef(type="integer", pattern="^[0-9]+$")}))
    errors = validate_schema_request(schema)
    check("16-pattern-on-non-string-caught", any("has a pattern but type 'integer'" in e for e in errors), errors)


def test_17_minmax_on_non_numeric_caught():
    schema = SchemaRequest(schemaCode="x", definition=SchemaDefinition(
        properties={"a": PropertyDef(type="string", minimum=1, maximum=10)}))
    errors = validate_schema_request(schema)
    check("17-minmax-on-non-numeric-caught", any("has a minimum/maximum but type 'string'" in e for e in errors), errors)


def test_18_minimum_greater_than_maximum_caught():
    schema = SchemaRequest(schemaCode="x", definition=SchemaDefinition(
        properties={"a": PropertyDef(type="number", minimum=10, maximum=1)}))
    errors = validate_schema_request(schema)
    check("18-min-gt-max-caught", any("minimum (10) greater than maximum (1)" in e for e in errors), errors)


def test_19_dangling_required_in_nested_group_caught():
    schema = SchemaRequest(schemaCode="x", definition=SchemaDefinition(properties={
        "address": PropertyDef(type="object", properties={"city": PropertyDef(type="string")},
                                required=["city", "ghostSubField"]),
    }))
    errors = validate_schema_request(schema)
    check("19-nested-dangling-required-caught",
          any("'ghostSubField' is listed as required under 'address'" in e for e in errors), errors)


def test_20_sub_fields_on_non_object_caught():
    schema = SchemaRequest(schemaCode="x", definition=SchemaDefinition(properties={
        "a": PropertyDef(type="string", properties={"b": PropertyDef(type="string")}),
    }))
    errors = validate_schema_request(schema)
    check("20-sub-fields-on-non-object-caught", any("has sub-fields but type 'string'" in e for e in errors), errors)


def test_21_pgr2_pattern_and_minmax_are_valid():
    """The real pgr2 schema's own pattern/minimum/maximum usage shouldn't trip the new checks --
    confirms 15-20 aren't trigger-happy against genuinely valid real-world constraints."""
    errors = validate_schema_request(build_pgr2().build())
    check("21-pgr2-no-false-positives", not errors, errors)


def test_22_minlength_maxlength_on_non_string_caught():
    schema = SchemaRequest(schemaCode="x", definition=SchemaDefinition(
        properties={"a": PropertyDef(type="integer", minLength=1, maxLength=10)}))
    errors = validate_schema_request(schema)
    check("22-minmaxlength-on-non-string-caught",
          any("has a minLength/maxLength but type 'integer'" in e for e in errors), errors)


def test_23_minlength_greater_than_maxlength_caught():
    schema = SchemaRequest(schemaCode="x", definition=SchemaDefinition(
        properties={"a": PropertyDef(type="string", minLength=10, maxLength=1)}))
    errors = validate_schema_request(schema)
    check("23-minlength-gt-maxlength-caught",
          any("minLength (10) greater than maxLength (1)" in e for e in errors), errors)


def build_password_field_schema() -> SchemaBuilder:
    """A synthetic example (not scraped -- see models.py's docstring on why the minLength/
    maxLength evidence is weaker than pattern/minimum/maximum's), matching the shape of a real
    8-128-char constraint found in license-certificate/Schema-Registry-3.0.0.yaml, applied there
    to an OpenAPI header parameter rather than a registry schema field."""
    b = SchemaBuilder("user-account")
    b.add_field("username", "string", required=True, minLength=2, maxLength=64)
    b.add_field("password", "string", required=True, minLength=8, maxLength=128)
    return b


def test_24_minlength_maxlength_builds_and_validates():
    schema = build_password_field_schema().build()
    errors = validate_schema_request(schema)
    check("24-builds-clean", not errors, errors)
    check("24-password-bounds",
          schema.definition.properties["password"].minLength == 8 and
          schema.definition.properties["password"].maxLength == 128)


# ---------------------------------------------------------------------------
# "Any possible JSON Schema" escape hatches: oneOf/anyOf, if/then conditionals,
# dependentRequired -- constructs PropertyDef can't represent, added on top of it rather than
# by extending it, per models.py's Union[PropertyDef, dict] / SchemaDefinition.allOf design.
# ---------------------------------------------------------------------------

def test_25_one_of_field_stored_as_raw_dict_and_round_trips():
    b = SchemaBuilder("x")
    name = b.add_one_of_field("Contact Method", [
        {"type": "object", "properties": {"email": {"type": "string"}}, "required": ["email"]},
        {"type": "object", "properties": {"phone": {"type": "string"}}, "required": ["phone"]},
    ], description="Either an email or a phone contact")
    schema = b.build()
    check("25-generated-name", name == "contactMethod", name)
    dumped = schema.model_dump(by_alias=True, exclude_none=True)
    check("25-oneof-preserved", "oneOf" in dumped["definition"]["properties"]["contactMethod"], dumped)
    check("25-two-alternatives",
          len(dumped["definition"]["properties"]["contactMethod"]["oneOf"]) == 2)
    check("25-validates-clean", not validate_schema_request(schema), validate_schema_request(schema))


def test_25b_one_of_field_normalizes_human_readable_property_names():
    """A real gap found via a live LLM run: the model handed over raw labels with spaces
    ("Email Address") as dict keys instead of camelCase identifiers, unlike every other field.
    add_one_of_field must normalize these the same way add_field/add_nested_field do."""
    b = SchemaBuilder("x")
    b.add_one_of_field("Contact Method", [
        {"properties": {"Email Address": {"type": "string", "format": "email"}},
         "required": ["Email Address"]},
        {"properties": {"Phone Number": {"type": "string", "pattern": "^[0-9]{10}$"}},
         "required": ["Phone Number"]},
    ])
    dumped = b.build().model_dump(by_alias=True, exclude_none=True)
    alt0 = dumped["definition"]["properties"]["contactMethod"]["oneOf"][0]
    alt1 = dumped["definition"]["properties"]["contactMethod"]["oneOf"][1]
    check("25b-camel-cased-key", "emailAddress" in alt0["properties"], alt0)
    check("25b-required-list-renamed", alt0["required"] == ["emailAddress"], alt0)
    check("25b-second-alt-camel-cased", "phoneNumber" in alt1["properties"], alt1)
    check("25b-second-alt-required-renamed", alt1["required"] == ["phoneNumber"], alt1)


def test_25c_one_of_field_strips_leaked_tool_bookkeeping_keys():
    """A real bug found via a live LLM run: the model sometimes carries its own
    required/required_stated/details_stated keys (meant only for add_field's arguments) into a
    oneOf alternative's sub-property definition. Those aren't real JSON Schema keywords and must
    not land in the schema that gets rendered and sent to the registry service."""
    b = SchemaBuilder("x")
    b.add_one_of_field("Contact Info", [
        {"properties": {"email": {"type": "string", "format": "email", "required": True,
                                   "required_stated": True, "details_stated": False}},
         "required": ["email"]},
    ])
    dumped = b.build().model_dump(by_alias=True, exclude_none=True)
    email_def = dumped["definition"]["properties"]["contactInfo"]["oneOf"][0]["properties"]["email"]
    check("25c-real-keywords-kept", email_def == {"type": "string", "format": "email"}, email_def)


def test_26_add_conditional_produces_correct_if_then_shape():
    b = SchemaBuilder("x")
    b.add_field("Applicant Type", "string", required=True, enum=["Individual", "Company"])
    b.add_field("Aadhaar Number", "string", pattern="^[0-9]{12}$")
    b.add_conditional("applicantType", "Individual", then_required=["aadhaarNumber"])
    schema = b.build()
    dumped = schema.model_dump(by_alias=True, exclude_none=True)
    check("26-allOf-present", len(dumped["definition"]["allOf"]) == 1, dumped)
    block = dumped["definition"]["allOf"][0]
    check("26-if-shape", block["if"] == {"properties": {"applicantType": {"const": "Individual"}},
                                          "required": ["applicantType"]}, block)
    check("26-then-shape", block["then"] == {"required": ["aadhaarNumber"]}, block)


def test_27_add_conditional_rejects_unknown_fields():
    b = SchemaBuilder("x")
    b.add_field("Applicant Type", "string", required=True)
    try:
        b.add_conditional("applicantType", "Individual", then_required=["ghostField"])
        check("27-should-have-raised", False, "no exception raised")
    except ValueError as e:
        check("27-rejects-unknown-then-field", "ghostField" in str(e), str(e))
    try:
        b.add_conditional("ghostTrigger", "X", then_required=[])
        check("27b-should-have-raised", False, "no exception raised")
    except ValueError as e:
        check("27b-rejects-unknown-trigger-field", "ghostTrigger" in str(e), str(e))


def test_28_add_dependent_required_produces_correct_shape_and_validates_fields():
    b = SchemaBuilder("x")
    b.add_field("Credit Card Number", "string")
    b.add_field("Cvv", "string")
    b.add_dependent_required("creditCardNumber", ["cvv"])
    schema = b.build()
    dumped = schema.model_dump(by_alias=True, exclude_none=True)
    check("28-dependent-required-shape",
          dumped["definition"]["dependentRequired"] == {"creditCardNumber": ["cvv"]}, dumped)

    try:
        b.add_dependent_required("ghost", ["cvv"])
        check("28b-should-have-raised", False, "no exception raised")
    except ValueError as e:
        check("28b-rejects-unknown-field", "ghost" in str(e), str(e))


# ---------------------------------------------------------------------------
# The remaining "any JSON Schema" escape hatches: dependentSchemas, patternProperties,
# $ref/$defs, and `not` -- the last constructs that used to fall back to a raw-JSON block.
# ---------------------------------------------------------------------------

def test_29_add_dependent_schema_produces_correct_shape_and_normalizes_names():
    b = SchemaBuilder("x")
    b.add_field("Credit Card Number", "string")
    b.add_dependent_schema("creditCardNumber", {
        "Cvv": {"type": "string", "pattern": "^[0-9]{3}$"},
        "Expiry Date": {"type": "string", "format": "date"},
    }, required=["Cvv"])
    dumped = b.build().model_dump(by_alias=True, exclude_none=True)
    block = dumped["definition"]["dependentSchemas"]["creditCardNumber"]
    check("29-camel-cased-keys", "cvv" in block["properties"] and "expiryDate" in block["properties"], block)
    check("29-required-renamed", block["required"] == ["cvv"], block)


def test_30_add_dependent_schema_rejects_unknown_trigger():
    b = SchemaBuilder("x")
    try:
        b.add_dependent_schema("ghost", {"a": {"type": "string"}})
        check("30-should-have-raised", False, "no exception raised")
    except ValueError as e:
        check("30-rejects-unknown-trigger", "ghost" in str(e), str(e))


def test_31_add_pattern_properties_produces_correct_shape():
    b = SchemaBuilder("x")
    b.add_field("Known Field", "string")
    b.add_pattern_properties("^x-", {"type": "string"})
    dumped = b.build().model_dump(by_alias=True, exclude_none=True)
    check("31-pattern-properties-shape",
          dumped["definition"]["patternProperties"] == {"^x-": {"type": "string"}}, dumped)


def test_32_add_pattern_properties_rejects_empty_pattern():
    b = SchemaBuilder("x")
    try:
        b.add_pattern_properties("", {"type": "string"})
        check("32-should-have-raised", False, "no exception raised")
    except ValueError as e:
        check("32-rejects-empty-pattern", "empty" in str(e), str(e))


def test_33_define_reusable_schema_and_add_ref_field_produce_correct_shape():
    b = SchemaBuilder("x")
    b.define_reusable_schema("Address", {
        "type": "object",
        "properties": {"city": {"type": "string"}, "pincode": {"type": "string", "pattern": "^[0-9]{6}$"}},
        "required": ["city"],
    })
    name = b.add_ref_field("Billing Address", "Address", required=True)
    dumped = b.build().model_dump(by_alias=True, exclude_none=True)
    check("33-generated-name", name == "billingAddress", name)
    check("33-ref-shape", dumped["definition"]["properties"]["billingAddress"] == {"$ref": "#/$defs/Address"}, dumped)
    check("33-defs-present", dumped["definition"]["$defs"]["Address"]["properties"]["city"] == {"type": "string"}, dumped)
    check("33-required-applied", "billingAddress" in dumped["definition"]["required"], dumped)


def test_34_add_ref_field_rejects_unknown_defs_name():
    b = SchemaBuilder("x")
    try:
        b.add_ref_field("Billing Address", "Ghost")
        check("34-should-have-raised", False, "no exception raised")
    except ValueError as e:
        check("34-rejects-unknown-defs-name", "Ghost" in str(e), str(e))


def test_34b_define_reusable_schema_strips_leaked_tool_bookkeeping_keys():
    """A real bug found via a live LLM run: the model carried its own
    required/required_stated/details_stated keys into $defs["Address"]'s own sub-properties, the
    same leak pattern already fixed for oneOf alternatives and dependentSchemas -- confirms
    define_reusable_schema normalizes its "properties" the same way."""
    b = SchemaBuilder("x")
    b.define_reusable_schema("Address", {
        "type": "object",
        "properties": {
            "City": {"type": "string", "required": True, "required_stated": False, "details_stated": False},
        },
        "required": ["City"],
    })
    dumped = b.build().model_dump(by_alias=True, exclude_none=True)
    city_def = dumped["definition"]["$defs"]["Address"]["properties"]["city"]
    check("34b-camel-cased-key", "city" in dumped["definition"]["$defs"]["Address"]["properties"], dumped)
    check("34b-real-keywords-only", city_def == {"type": "string"}, city_def)
    check("34b-required-renamed", dumped["definition"]["$defs"]["Address"]["required"] == ["city"], dumped)


def test_35_add_not_constraint_mutates_existing_field():
    b = SchemaBuilder("x")
    b.add_field("Username", "string", required=True)
    b.add_not_constraint("username", {"pattern": "^admin$"})
    dumped = b.build().model_dump(by_alias=True, exclude_none=True)
    check("35-not-shape", dumped["definition"]["properties"]["username"]["not"] == {"pattern": "^admin$"}, dumped)


def test_36_add_not_constraint_rejects_unknown_field():
    b = SchemaBuilder("x")
    try:
        b.add_not_constraint("ghost", {"pattern": "^x$"})
        check("36-should-have-raised", False, "no exception raised")
    except ValueError as e:
        check("36-rejects-unknown-field", "ghost" in str(e), str(e))


def test_37_add_not_constraint_works_on_oneof_style_raw_dict_property():
    b = SchemaBuilder("x")
    b.add_one_of_field("Contact", [
        {"properties": {"email": {"type": "string"}}, "required": ["email"]},
    ])
    b.add_not_constraint("contact", {"const": "banned"})
    dumped = b.build().model_dump(by_alias=True, exclude_none=True)
    check("37-not-on-raw-dict", dumped["definition"]["properties"]["contact"]["not"] == {"const": "banned"}, dumped)


def test_38_add_raw_property_stores_verbatim_and_applies_required():
    b = SchemaBuilder("x")
    name = b.add_raw_property("Tags", {
        "type": "array", "prefixItems": [{"type": "string"}, {"type": "integer"}],
    }, required=True)
    dumped = b.build().model_dump(by_alias=True, exclude_none=True)
    check("38-generated-name", name == "tags", name)
    check("38-stored-verbatim", dumped["definition"]["properties"]["tags"]["prefixItems"] ==
          [{"type": "string"}, {"type": "integer"}], dumped)
    check("38-required-applied", "tags" in dumped["definition"]["required"], dumped)


def test_39_add_raw_property_normalizes_its_own_named_properties():
    b = SchemaBuilder("x")
    b.add_raw_property("Weird Group", {
        "type": "object",
        "properties": {"City Name": {"type": "string", "required": True, "required_stated": False}},
        "required": ["City Name"],
    })
    dumped = b.build().model_dump(by_alias=True, exclude_none=True)
    group = dumped["definition"]["properties"]["weirdGroup"]
    check("39-camel-cased-key", "cityName" in group["properties"], group)
    check("39-real-keywords-only", group["properties"]["cityName"] == {"type": "string"}, group)
    check("39-required-renamed", group["required"] == ["cityName"], group)


def test_40_unmodeled_top_level_keyword_round_trips_instead_of_vanishing():
    """extra="allow" on SchemaDefinition/SchemaRequest: a top-level keyword this project doesn't
    explicitly model (e.g. `title`, or a real registry field like `x-ref-schema`) must survive a
    build -> model_dump -> model_validate round trip byte-for-byte, not silently disappear the way
    Pydantic's default `extra="ignore"` would have dropped it."""
    schema = SchemaRequest(schemaCode="x", definition=SchemaDefinition(
        properties={"name": PropertyDef(type="string")},
    ), **{"title": "Applicant Registry", "x-ref-schema": "some-other-schema"})
    dumped = schema.model_dump(by_alias=True, exclude_none=True)
    check("40-schema-request-extra-present", dumped["title"] == "Applicant Registry", dumped)
    check("40-schema-request-extra-alias-key", dumped["x-ref-schema"] == "some-other-schema", dumped)

    reloaded = SchemaRequest.model_validate(dumped)
    check("40-round-trips-through-validate", reloaded.model_dump(by_alias=True, exclude_none=True) == dumped, dumped)

    definition = SchemaDefinition(properties={}, **{"$comment": "internal note", "$id": "https://example.com/s"})
    dumped_def = definition.model_dump(by_alias=True, exclude_none=True)
    check("40-definition-extra-comment", dumped_def["$comment"] == "internal note", dumped_def)
    check("40-definition-extra-id", dumped_def["$id"] == "https://example.com/s", dumped_def)


if __name__ == "__main__":
    test_functions = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in test_functions:
        fn()
    print(f"PASSED: {len(PASSED)} check(s) -> {PASSED}")
    print("\nAll SchemaBuilder + completeness checks verified against the real registry API shape.")
