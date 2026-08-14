import io
import json
from types import SimpleNamespace

from src import mistral_client


class FakeOcr:
    def __init__(self, pages, document_annotation=None):
        self.pages = pages
        self.document_annotation = document_annotation
        self.calls = []

    def process(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            pages=self.pages,
            document_annotation=self.document_annotation,
        )


class FakeFiles:
    def __init__(self, downloads=None):
        self.downloads = downloads or {}
        self.deleted = []
        self.uploads = []

    def upload(self, **kwargs):
        self.uploads.append(kwargs)
        return SimpleNamespace(id="file-1")

    def get_signed_url(self, **_kwargs):
        return SimpleNamespace(url="https://example.invalid/document.pdf")

    def download(self, *, file_id):
        return io.BytesIO(self.downloads[file_id])

    def delete(self, *, file_id):
        self.deleted.append(file_id)


class FakeBatchJobs:
    def __init__(self, output_file="batch-output"):
        self.output_file = output_file
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            id="batch-job-1",
            status="SUCCESS",
            output_file=self.output_file,
            error_file=None,
        )


def test_pdf_ocr_requests_markdown_without_layout_blocks(monkeypatch):
    page = SimpleNamespace(markdown="# Embedded result", images=[])
    fake = SimpleNamespace(files=FakeFiles(), ocr=FakeOcr([page]))
    monkeypatch.setattr(mistral_client, "get_client", lambda: fake)

    result = mistral_client.ocr_pdf_pages_markdown(b"pdf", include_images=False)

    assert result == ["# Embedded result"]
    assert fake.ocr.calls[0]["model"] == "mistral-ocr-latest"
    assert fake.ocr.calls[0]["document"]["type"] == "document_url"
    assert fake.ocr.calls[0]["confidence_scores_granularity"] == "page"
    assert "include_blocks" not in fake.ocr.calls[0]


def test_image_ocr_requests_markdown_without_layout_blocks(monkeypatch):
    page = SimpleNamespace(markdown="Image result")
    fake = SimpleNamespace(ocr=FakeOcr([page]))
    monkeypatch.setattr(mistral_client, "get_client", lambda: fake)

    result = mistral_client.ocr_image_markdown(b"image")

    assert result == "Image result"
    assert fake.ocr.calls[0]["model"] == "mistral-ocr-latest"
    assert fake.ocr.calls[0]["document"]["type"] == "image_url"
    assert fake.ocr.calls[0]["confidence_scores_granularity"] == "page"
    assert "include_blocks" not in fake.ocr.calls[0]


def test_asset_aware_ocr_keeps_exact_markdown_and_image_payload(monkeypatch):
    image = SimpleNamespace(
        id="figure.jpeg",
        image_base64="data:image/jpeg;base64,aW1hZ2U=",
    )
    page = SimpleNamespace(
        markdown="![figure](figure.jpeg)",
        images=[image],
        confidence_scores=SimpleNamespace(average_page_confidence_score=0.93),
    )
    fake = SimpleNamespace(files=FakeFiles(), ocr=FakeOcr([page]))
    monkeypatch.setattr(mistral_client, "get_client", lambda: fake)

    result = mistral_client.ocr_pdf_pages_with_assets(b"pdf")

    assert result[0].markdown == "![figure](figure.jpeg)"
    assert result[0].images[0].source_ref == "figure.jpeg"
    assert result[0].images[0].data_url == "data:image/jpeg;base64,aW1hZ2U="
    assert result[0].confidence_score == 0.93


def test_structured_markdown_keeps_table_and_display_math_lines_intact():
    source = (
        "| Metric | Formula |\n"
        "|---|---|\n"
        "| Utilization | $\\rho = \\lambda / \\mu$ |\n\n"
        "$$\n"
        "\\begin{array}{l}\n"
        "\\pi_0 = \\frac{1 - \\rho}{1 - \\rho^{N + 1}} \\\\\n"
        "\\end{array}\n"
        "$$"
    )

    result = mistral_client._fix_markdown_line_breaks(source, parse_structured_md=True)

    assert result == source


def test_document_annotation_uses_strict_schema_without_changing_markdown(monkeypatch):
    annotation = '{"supplier_name":"Kedai Laju","total_amount":12.5}'
    fake = SimpleNamespace(files=FakeFiles(), ocr=FakeOcr([], annotation))
    monkeypatch.setattr(mistral_client, "get_client", lambda: fake)
    schema = {
        "type": "object",
        "properties": {"supplier_name": {"type": "string"}},
        "required": ["supplier_name"],
    }

    result = mistral_client.extract_document_annotation(
        b"pdf",
        is_pdf=True,
        mime_type="application/pdf",
        schema_name="invoice_receipt",
        schema=schema,
        prompt="Extract printed fields only",
    )

    assert result == {"supplier_name": "Kedai Laju", "total_amount": 12.5}
    call = fake.ocr.calls[0]
    assert call["model"] == "mistral-ocr-latest"
    assert call["document_annotation_format"]["type"] == "json_schema"
    assert call["document_annotation_format"]["json_schema"]["strict"] is True
    assert call["document_annotation_prompt"] == "Extract printed fields only"


def test_combined_ocr_returns_markdown_and_annotation_from_one_request(monkeypatch):
    page = SimpleNamespace(
        markdown="# Receipt",
        images=[],
        confidence_scores=SimpleNamespace(average_page_confidence_score=0.97),
    )
    annotation = '{"supplier_name":"Kedai Laju","total_amount":12.5}'
    fake = SimpleNamespace(files=FakeFiles(), ocr=FakeOcr([page], annotation))
    monkeypatch.setattr(mistral_client, "get_client", lambda: fake)

    result = mistral_client.ocr_document_with_annotation(
        b"pdf",
        is_pdf=True,
        mime_type="application/pdf",
        schema_name="invoice_receipt",
        schema={
            "type": "object",
            "properties": {"supplier_name": {"type": "string"}},
            "required": ["supplier_name"],
        },
        prompt="Extract printed fields only",
    )

    assert len(fake.ocr.calls) == 1
    assert result.pages[0].markdown == "# Receipt"
    assert result.pages[0].confidence_score == 0.97
    assert result.document_annotation["supplier_name"] == "Kedai Laju"
    call = fake.ocr.calls[0]
    assert call["include_image_base64"] is True
    assert call["document_annotation_format"]["type"] == "json_schema"


def test_image_batch_maps_out_of_order_results_and_cleans_remote_output(monkeypatch):
    output = "\n".join([
        json.dumps({
            "custom_id": "2",
            "response": {
                "status_code": 200,
                "body": {"pages": [{"markdown": "# Page 2", "images": []}]},
            },
        }),
        json.dumps({
            "custom_id": "1",
            "response": {
                "status_code": 200,
                "body": {"pages": [{"markdown": "# Page 1", "images": []}]},
            },
        }),
    ]).encode()
    files = FakeFiles({"batch-output": output})
    jobs = FakeBatchJobs()
    fake = SimpleNamespace(files=files, batch=SimpleNamespace(jobs=jobs))
    monkeypatch.setattr(mistral_client, "get_client", lambda: fake)

    result = mistral_client.ocr_images_batch({
        "1": (b"first", "image/png"),
        "2": (b"second", "image/png"),
    })

    assert result.pages["1"].markdown == "# Page 1"
    assert result.pages["2"].markdown == "# Page 2"
    assert result.remote_job_id == "batch-job-1"
    assert files.deleted == ["batch-output", "file-1"]
    assert jobs.calls[0]["endpoint"] == "/v1/ocr"
    assert jobs.calls[0]["input_files"] == ["file-1"]
    assert files.uploads[0]["purpose"] == "batch"
    uploaded_lines = files.uploads[0]["file"]["content"].decode().splitlines()
    assert [json.loads(line)["custom_id"] for line in uploaded_lines] == ["1", "2"]
