"""Tests: SchemaBuilder + validate_schema_request, mirroring
../workflow-prototype/test_workflow_builder.py's role for the registry side -- the real
license-registry example (matching the actual registry service's own Postman collection payload)
built entirely through builder calls, plus one test per completeness check.
"""

from builder import SchemaBuilder, camel_field_name
from models import IndexDef, PropertyDef, SchemaDefinition, SchemaRequest
from validate import validate_schema_request

PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name} FAILED: {detail}"
    PASSED.append(name)


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
    check("01-unique-on-license-number", schema.definition.x_unique == [["licenseNumber"]])
    check("01-index-on-status", schema.definition.x_indexes[0].fieldPath == "status")


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
        properties={"a": PropertyDef(type="string")}, **{"x-unique": [["a", "ghost"]]}))
    errors = validate_schema_request(schema)
    check("07-dangling-unique-caught", any("unique constraint references 'ghost'" in e for e in errors), errors)


def test_08_dangling_index_caught():
    schema = SchemaRequest(schemaCode="x", definition=SchemaDefinition(
        properties={"a": PropertyDef(type="string")}, **{"x-indexes": [IndexDef(fieldPath="ghost")]}))
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


if __name__ == "__main__":
    test_functions = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in test_functions:
        fn()
    print(f"PASSED: {len(PASSED)} check(s) -> {PASSED}")
    print("\nAll SchemaBuilder + completeness checks verified against the real registry API shape.")
