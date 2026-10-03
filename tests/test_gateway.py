"""Exercise the real SDK transport and PDF pipeline without sending documents externally."""

import base64
import json

import httpx
import pymupdf
import pytest
from click.testing import CliRunner
from openai import OpenAI
from PIL import Image

import riordino as r


def service(handler, mode="json_schema", retries=0):
    client = OpenAI(
        api_key="test-only",
        base_url="https://gateway.test/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return r.OpenAIService(client, "vision-alias", retries, r.load_prompts(), mode)


def completion(payload, finish="stop", refusal=None):
    return httpx.Response(
        200,
        json={
            "id": "test",
            "object": "chat.completion",
            "created": 0,
            "model": "vision-alias",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish,
                    "message": {"role": "assistant", "content": json.dumps(payload), "refusal": refusal},
                }
            ],
        },
    )


def analysis():
    return {
        "title": "Invoice",
        "description": "Invoice page",
        "detailed_analysis": "Invoice A",
        "document_type": "invoice",
        "priority": "normal",
    }


def test_multimodal_transport_and_schema():
    requests = []

    def handler(request):
        requests.append(request)
        return completion({"pages": [analysis()]})

    pages = service(handler).analyze_batch([r.RenderedPage(0, Image.new("RGB", (8, 8), "white"))], ["English"])
    assert pages[0].title == "Invoice"
    request = requests[0]
    assert str(request.url) == "https://gateway.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-only"
    body = json.loads(request.content)
    assert body["model"] == "vision-alias"
    parts = body["messages"][0]["content"]
    image = next(p["image_url"]["url"] for p in parts if p["type"] == "image_url")
    assert base64.b64decode(image.split(",")[1]).startswith(b"\x89PNG")
    schema = body["response_format"]["json_schema"]["schema"]
    page_schema = schema["$defs"]["PageAnalysis"]
    assert page_schema["additionalProperties"] is False
    assert set(page_schema["required"]) == set(page_schema["properties"])
    assert "default" not in page_schema["properties"]["date"]
    assert "thinking_config" not in body


@pytest.mark.parametrize("mode", ["json_schema", "json_object"])
def test_grouping_and_ordering_use_selected_transport(mode):
    requests = []
    group = {"title": "A", "suggested_filename": "a", "page_indices": [0, 1], "summary": "A", "priority": "normal"}
    replies = [{"documents": [group]}, {"page_indices": [1, 0]}]

    def handler(request):
        requests.append(json.loads(request.content))
        return completion(replies.pop(0))

    model = service(handler, mode)
    analyses = [r.PageAnalysis(**analysis()) for _ in range(2)]
    groups = model.aggregate(analyses)
    assert model.order_group(groups.documents[0], analyses, [Image.new("RGB", (2, 2))] * 2) == [1, 0]
    assert all(req["response_format"]["type"] == mode for req in requests)


@pytest.mark.parametrize(
    "payload,finish,refusal",
    [({}, "stop", None), ({"page_indices": [0]}, "length", None), ({"page_indices": [0]}, "stop", "refused")],
)
def test_invalid_output_fails_closed(payload, finish, refusal):
    with pytest.raises(r.ModelResponseError):
        service(lambda request: completion(payload, finish, refusal))._generate_text("order", r.OrderingResult)


def test_auth_errors_are_not_retried_or_leaked():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={"error": {"message": "SECRET_DOCUMENT_AND_KEY"}})

    with pytest.raises(r.CliError) as exc:
        service(handler, retries=3)._generate_text("order", r.OrderingResult)
    assert len(calls) == 1
    assert "401" in str(exc.value)
    assert "SECRET" not in str(exc.value)


def test_rate_limit_retry_then_success():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"message": "try later"}})
        return completion({"page_indices": [0]})

    assert service(handler, retries=1)._generate_text("order", r.OrderingResult).page_indices == [0]
    assert len(calls) == 2


def test_cli_selects_gateway_and_named_secret(monkeypatch, tmp_path):
    monkeypatch.setenv("RIORDINO_PROVIDER", "openai")
    monkeypatch.setenv("RIORDINO_BASE_URL", "http://localhost:4000/v1")
    monkeypatch.setenv("RIORDINO_MODEL", "local-vision")
    monkeypatch.setenv("RIORDINO_API_KEY_ENV", "TEST_GATEWAY_KEY")
    monkeypatch.setenv("TEST_GATEWAY_KEY", "not-a-real-key")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    captured = []
    monkeypatch.setattr(r, "run_pipeline", captured.append)
    result = CliRunner().invoke(r.main, [str(tmp_path / "in.pdf"), "--skip-rotation"])
    assert result.exit_code == 0, result.output
    model = r.create_service(captured[0], r.load_prompts())
    assert isinstance(model, r.OpenAIService)
    assert str(model.client.base_url) == "http://localhost:4000/v1/"
    assert model.client.max_retries == 0
    assert model.client.api_key == "not-a-real-key"
    assert "not-a-real-key" not in repr(captured[0])


def test_gateway_requires_explicit_model():
    result = CliRunner().invoke(r.main, ["in.pdf", "--provider", "openai", "--skip-rotation"])
    assert result.exit_code == 1
    assert "--model" in result.output


def test_real_pdf_pipeline_preserves_input(monkeypatch, tmp_path):
    source = tmp_path / "batch.pdf"
    with pymupdf.open() as doc:
        for label in ["Invoice A page 1", "Invoice A page 2", "Letter B"]:
            doc.new_page().insert_text((72, 72), label)
        doc.save(source)
    original = source.read_bytes()
    replies = [
        {"pages": [analysis() for _ in range(3)]},
        {
            "documents": [
                {"title": "A", "suggested_filename": "a", "page_indices": [0, 1], "summary": "A", "priority": "normal"},
                {"title": "B", "suggested_filename": "b", "page_indices": [2], "summary": "B", "priority": "normal"},
            ]
        },
    ]
    model = service(lambda request: completion(replies.pop(0)))
    monkeypatch.setattr(r, "create_service", lambda options, prompts: model)
    options = r.PipelineOptions(
        input_paths=[source],
        output_dir=tmp_path / "out",
        blank_threshold=0.001,
        dpi=72,
        model="vision-alias",
        provider="openai",
        batch_size=10,
        max_retries=0,
        dry_run=False,
        languages=["en"],
        skip_blanks=True,
        skip_rotation=True,
        skip_ordering=True,
    )
    r.run_pipeline(options)
    assert source.read_bytes() == original
    outputs = sorted((tmp_path / "out").glob("*.pdf"))
    assert len(outputs) == 2
    with pymupdf.open(outputs[0]) as a, pymupdf.open(outputs[1]) as b:
        assert [a.page_count, b.page_count] == [2, 1]
        assert "Invoice A page 2" in a[1].get_text()
        assert "Letter B" in b[0].get_text()


@pytest.mark.parametrize("groups", [[[0], [0]], [[0]], [[0, 2]], [[], [0, 1]]])
def test_bad_page_groups_cannot_drop_or_duplicate_pages(groups):
    payload = {
        "documents": [
            {"title": "A", "suggested_filename": "a", "page_indices": pages, "summary": "A", "priority": "normal"}
            for pages in groups
        ]
    }
    model = service(lambda request: completion(payload))
    with pytest.raises(r.ModelResponseError, match="every page exactly once"):
        model.aggregate([r.PageAnalysis(**analysis()) for _ in range(2)])


def test_native_gemini_transport_still_works():
    from types import SimpleNamespace

    calls = []

    def generate_content(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=json.dumps({"pages": [analysis()]}))

    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    model = r.GeminiService(client, "gemini-test", 0, r.load_prompts())
    assert model.analyze_batch([r.RenderedPage(0, Image.new("RGB", (8, 8)))], ["English"])[0].title == "Invoice"
    assert calls[0]["model"] == "gemini-test"
    assert calls[0]["config"].response_schema is r.BatchAnalysisResult
