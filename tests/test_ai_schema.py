from job_mail_assistant.ai_parser import _strict_json_schema
from job_mail_assistant.models import ParsedEmail


def test_structured_output_schema_requires_all_object_properties() -> None:
    schema = _strict_json_schema(ParsedEmail.model_json_schema())
    assert set(schema["required"]) == set(schema["properties"])
    time_schema = schema["$defs"]["TimeExpression"]
    assert set(time_schema["required"]) == set(time_schema["properties"])
    assert "default" not in time_schema["properties"]["year"]
