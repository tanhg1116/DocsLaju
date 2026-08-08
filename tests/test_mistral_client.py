from types import SimpleNamespace

from src import mistral_client


class FakeOcr:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def process(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(pages=self.pages)


class FakeFiles:
    def upload(self, **_kwargs):
        return SimpleNamespace(id="file-1")

    def get_signed_url(self, **_kwargs):
        return SimpleNamespace(url="https://example.invalid/document.pdf")


def test_pdf_ocr_requests_markdown_without_layout_blocks(monkeypatch):
    page = SimpleNamespace(markdown="# Embedded result", images=[])
    fake = SimpleNamespace(files=FakeFiles(), ocr=FakeOcr([page]))
    monkeypatch.setattr(mistral_client, "get_client", lambda: fake)

    result = mistral_client.ocr_pdf_pages_markdown(b"pdf", include_images=False)

    assert result == ["# Embedded result"]
    assert fake.ocr.calls[0]["model"] == "mistral-ocr-latest"
    assert fake.ocr.calls[0]["document"]["type"] == "document_url"
    assert "include_blocks" not in fake.ocr.calls[0]


def test_image_ocr_requests_markdown_without_layout_blocks(monkeypatch):
    page = SimpleNamespace(markdown="Image result")
    fake = SimpleNamespace(ocr=FakeOcr([page]))
    monkeypatch.setattr(mistral_client, "get_client", lambda: fake)

    result = mistral_client.ocr_image_markdown(b"image")

    assert result == "Image result"
    assert fake.ocr.calls[0]["model"] == "mistral-ocr-latest"
    assert fake.ocr.calls[0]["document"]["type"] == "image_url"
    assert "include_blocks" not in fake.ocr.calls[0]


def test_asset_aware_ocr_keeps_exact_markdown_and_image_payload(monkeypatch):
    image = SimpleNamespace(
        id="figure.jpeg",
        image_base64="data:image/jpeg;base64,aW1hZ2U=",
    )
    page = SimpleNamespace(markdown="![figure](figure.jpeg)", images=[image])
    fake = SimpleNamespace(files=FakeFiles(), ocr=FakeOcr([page]))
    monkeypatch.setattr(mistral_client, "get_client", lambda: fake)

    result = mistral_client.ocr_pdf_pages_with_assets(b"pdf")

    assert result[0].markdown == "![figure](figure.jpeg)"
    assert result[0].images[0].source_ref == "figure.jpeg"
    assert result[0].images[0].data_url == "data:image/jpeg;base64,aW1hZ2U="


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
