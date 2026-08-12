import io

from PIL import Image, ImageDraw

from src.services.image_preprocessor import preprocess_image
from src.mistral_client import OcrPageResult


def _png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _page_content(draw: ImageDraw.ImageDraw, left: int, top: int, right: int) -> None:
    draw.text((left, top), "QUOTATION Q-1042", fill="black")
    for offset in range(45, 520, 34):
        draw.line((left, top + offset, right, top + offset), fill=(60, 60, 60), width=2)


def test_clean_high_resolution_image_is_not_blindly_transformed():
    image = Image.new("RGB", (1600, 2000), "white")
    _page_content(ImageDraw.Draw(image), 150, 180, 1450)
    content = _png_bytes(image)

    result = preprocess_image(content, "image/png")

    assert result.report["actions"] == []
    assert result.report["used_original"] is True
    assert result.report["original_size"] == [1600, 2000]
    assert result.report["ocr_size"] == [1600, 2000]


def test_webpage_chrome_is_cropped_only_when_page_component_is_confident():
    image = Image.new("RGB", (900, 1100), (207, 211, 218))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 899, 82), fill=(225, 228, 233))
    draw.text((25, 28), "Print     Download PDF     Share", fill=(70, 75, 83))
    draw.rectangle((55, 105, 845, 1040), fill="white")
    _page_content(draw, 105, 155, 790)

    result = preprocess_image(_png_bytes(image), "image/png")

    assert "cropped_page_or_webpage_border" in result.report["actions"]
    assert result.report["crop_confidence"] >= 0.78
    assert result.report["ocr_size"][1] / result.report["ocr_size"][0] > 1.0


def test_low_resolution_image_is_upscaled_but_original_dimensions_are_retained_in_report():
    image = Image.new("RGB", (420, 600), "white")
    _page_content(ImageDraw.Draw(image), 25, 35, 390)

    result = preprocess_image(_png_bytes(image), "image/png")

    assert any(action.startswith("upscaled_") for action in result.report["actions"])
    assert result.report["original_size"] == [420, 600]
    assert min(result.report["ocr_size"]) >= 1170
    assert result.mime_type == "image/png"


def test_image_ocr_uses_optimized_copy_and_keeps_original_document_bytes(monkeypatch):
    from app import _infer_document_page

    original = _png_bytes(Image.new("RGB", (320, 480), "white"))
    captured = {}

    def fake_ocr(content, *, mime_type, model):
        captured.update(content=content, mime_type=mime_type, model=model)
        return OcrPageResult("# Extracted")

    monkeypatch.setattr("app.ocr_image_with_assets", fake_ocr)
    document = {
        "id": "doc-1",
        "name": "screen.png",
        "content": original,
        "mime_type": "image/png",
        "is_pdf": False,
    }

    markdown = _infer_document_page(document, 1)

    assert document["content"] == original
    assert captured["content"] != original
    assert captured["mime_type"] == "image/png"
    assert str(markdown) == "# Extracted"
    assert any(
        action.startswith("upscaled_")
        for action in markdown.preprocessing_report["actions"]
    )
