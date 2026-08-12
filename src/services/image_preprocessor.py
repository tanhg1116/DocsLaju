from __future__ import annotations

import io
import math
from dataclasses import asdict, dataclass

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps, ImageStat


MAX_ANALYSIS_SIDE = 420
MAX_OCR_SIDE = 3600
MIN_OCR_SHORT_SIDE = 1200


@dataclass(frozen=True)
class ImageMetrics:
    width: int
    height: int
    contrast: float
    sharpness: float
    noise: float
    quality: float


@dataclass(frozen=True)
class PreprocessedImage:
    content: bytes
    mime_type: str
    report: dict


def _percentile(histogram: list[int], fraction: float) -> int:
    target = sum(histogram) * fraction
    cumulative = 0
    for value, count in enumerate(histogram):
        cumulative += count
        if cumulative >= target:
            return value
    return 255


def _analysis_copy(image: Image.Image, maximum: int = MAX_ANALYSIS_SIDE) -> Image.Image:
    copy = image.convert("L")
    if max(copy.size) > maximum:
        scale = maximum / max(copy.size)
        copy = copy.resize(
            (max(1, round(copy.width * scale)), max(1, round(copy.height * scale))),
            Image.Resampling.BILINEAR,
        )
    return copy


def image_metrics(image: Image.Image) -> ImageMetrics:
    gray = _analysis_copy(image)
    histogram = gray.histogram()
    contrast = float(_percentile(histogram, 0.95) - _percentile(histogram, 0.05))
    edges = gray.filter(ImageFilter.FIND_EDGES)
    if edges.width > 4 and edges.height > 4:
        edges = edges.crop((2, 2, edges.width - 2, edges.height - 2))
    sharpness = float(ImageStat.Stat(edges).var[0])
    median = gray.filter(ImageFilter.MedianFilter(3))
    noise = float(ImageStat.Stat(ImageChops.difference(gray, median)).mean[0])
    resolution = min(1.0, min(image.size) / MIN_OCR_SHORT_SIDE)
    quality = (
        0.35 * min(1.0, contrast / 150.0)
        + 0.45 * min(1.0, sharpness / 900.0)
        + 0.20 * resolution
    )
    return ImageMetrics(
        image.width,
        image.height,
        round(contrast, 2),
        round(sharpness, 2),
        round(noise, 2),
        round(quality, 4),
    )


def _largest_bright_component_crop(image: Image.Image) -> tuple[tuple[int, int, int, int] | None, float]:
    """Find a page-like bright rectangle separated from darker screenshot chrome."""
    gray = _analysis_copy(image, 360)
    width, height = gray.size
    pixels = gray.tobytes()
    bright = bytearray(1 if value >= 222 else 0 for value in pixels)
    visited = bytearray(width * height)
    best: tuple[int, int, int, int, int] | None = None

    for start in range(width * height):
        if not bright[start] or visited[start]:
            continue
        stack = [start]
        visited[start] = 1
        count = 0
        min_x = max_x = start % width
        min_y = max_y = start // width
        while stack:
            index = stack.pop()
            x = index % width
            y = index // width
            count += 1
            min_x = min(min_x, x)
            max_x = max(max_x, x)
            min_y = min(min_y, y)
            max_y = max(max_y, y)
            if x and bright[index - 1] and not visited[index - 1]:
                visited[index - 1] = 1
                stack.append(index - 1)
            if x + 1 < width and bright[index + 1] and not visited[index + 1]:
                visited[index + 1] = 1
                stack.append(index + 1)
            if y and bright[index - width] and not visited[index - width]:
                visited[index - width] = 1
                stack.append(index - width)
            if y + 1 < height and bright[index + width] and not visited[index + width]:
                visited[index + width] = 1
                stack.append(index + width)
        if best is None or count > best[0]:
            best = (count, min_x, min_y, max_x, max_y)

    if not best:
        return None, 0.0
    count, left, top, right, bottom = best
    component_width = right - left + 1
    component_height = bottom - top + 1
    bbox_area = component_width * component_height
    image_area = width * height
    fill_ratio = count / max(1, bbox_area)
    area_ratio = bbox_area / max(1, image_area)
    aspect_ratio = component_width / max(1, component_height)
    removed_ratio = 1.0 - area_ratio
    if (
        component_width < width * 0.52
        or component_height < height * 0.48
        or fill_ratio < 0.52
        or area_ratio < 0.34
        or not 0.28 <= aspect_ratio <= 2.4
        or removed_ratio < 0.035
        or removed_ratio > 0.48
    ):
        return None, 0.0

    margin = max(2, round(min(width, height) * 0.012))
    left = max(0, left - margin)
    top = max(0, top - margin)
    right = min(width, right + margin + 1)
    bottom = min(height, bottom + margin + 1)
    scale_x = image.width / width
    scale_y = image.height / height
    crop = (
        max(0, math.floor(left * scale_x)),
        max(0, math.floor(top * scale_y)),
        min(image.width, math.ceil(right * scale_x)),
        min(image.height, math.ceil(bottom * scale_y)),
    )
    confidence = min(
        0.99,
        0.45 + 0.30 * min(1.0, fill_ratio) + 0.25 * min(1.0, removed_ratio / 0.18),
    )
    return crop, round(confidence, 3)


def _projection_score(edge_image: Image.Image) -> float:
    width, height = edge_image.size
    pixels = edge_image.load()
    row_sums = [
        sum(1 for x in range(width) if pixels[x, y] >= 72)
        for y in range(height)
    ]
    mean = sum(row_sums) / max(1, len(row_sums))
    return sum((value - mean) ** 2 for value in row_sums) / max(1, len(row_sums))


def _deskew_angle(image: Image.Image) -> tuple[float, float]:
    gray = _analysis_copy(image, 700)
    edges = gray.filter(ImageFilter.FIND_EDGES)
    base_score = _projection_score(edges)
    best_angle = 0.0
    best_score = base_score
    for angle in (-3.0, -2.0, -1.0, 1.0, 2.0, 3.0):
        candidate = edges.rotate(angle, Image.Resampling.BILINEAR, expand=False, fillcolor=0)
        score = _projection_score(candidate)
        if score > best_score:
            best_angle, best_score = angle, score
    improvement = best_score / max(1.0, base_score)
    return (best_angle, improvement) if improvement >= 1.16 else (0.0, improvement)


def _encode(image: Image.Image, prefer_jpeg: bool) -> tuple[bytes, str]:
    output = io.BytesIO()
    if prefer_jpeg:
        image.convert("RGB").save(output, format="JPEG", quality=95, optimize=True)
        return output.getvalue(), "image/jpeg"
    image.save(output, format="PNG", optimize=True)
    return output.getvalue(), "image/png"


def preprocess_image(content: bytes, mime_type: str) -> PreprocessedImage:
    """Conditionally improve OCR input while retaining the original upload elsewhere."""
    with Image.open(io.BytesIO(content)) as source:
        source.load()
        original_format = (source.format or "").upper()
        original_size = source.size
        orientation = source.getexif().get(274, 1)
        image = ImageOps.exif_transpose(source)
        actions: list[str] = []
        if orientation not in (None, 1):
            actions.append("corrected_orientation")
        if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
            canvas = Image.new("RGB", image.size, "white")
            alpha = image.convert("RGBA")
            canvas.paste(alpha, mask=alpha.getchannel("A"))
            image = canvas
            actions.append("flattened_transparency")
        else:
            image = image.convert("RGB")

    before = image_metrics(image)
    crop, crop_confidence = _largest_bright_component_crop(image)
    if crop and crop_confidence >= 0.78:
        candidate = image.crop(crop)
        retained = candidate.width * candidate.height / max(1, image.width * image.height)
        if 0.52 <= retained <= 0.965:
            image = candidate
            actions.append("cropped_page_or_webpage_border")
        else:
            crop_confidence = 0.0

    angle, deskew_improvement = _deskew_angle(image)
    if angle:
        image = image.rotate(angle, Image.Resampling.BICUBIC, expand=True, fillcolor="white")
        actions.append(f"deskewed_{angle:+g}_degrees")

    short_side = min(image.size)
    long_side = max(image.size)
    if short_side < MIN_OCR_SHORT_SIDE:
        factor = min(3.0, MIN_OCR_SHORT_SIDE / short_side, MAX_OCR_SIDE / long_side)
        if factor >= 1.18:
            image = image.resize(
                (round(image.width * factor), round(image.height * factor)),
                Image.Resampling.LANCZOS,
            )
            actions.append(f"upscaled_{factor:.1f}x")

    current = image_metrics(image)
    if current.noise >= 11.0:
        candidate = image.filter(ImageFilter.MedianFilter(3))
        candidate_metrics = image_metrics(candidate)
        if candidate_metrics.noise <= current.noise * 0.82 and candidate_metrics.sharpness >= current.sharpness * 0.68:
            image, current = candidate, candidate_metrics
            actions.append("denoised")

    if current.contrast < 92.0:
        candidate = ImageEnhance.Contrast(image).enhance(1.18)
        candidate_metrics = image_metrics(candidate)
        if candidate_metrics.contrast >= current.contrast + 4 and candidate_metrics.sharpness >= current.sharpness * 0.85:
            image, current = candidate, candidate_metrics
            actions.append("enhanced_contrast")

    if current.sharpness < 520.0:
        candidate = image.filter(ImageFilter.UnsharpMask(radius=1.1, percent=85, threshold=3))
        candidate_metrics = image_metrics(candidate)
        if candidate_metrics.sharpness >= current.sharpness * 1.08 and candidate_metrics.noise <= max(5.0, current.noise * 1.7):
            image, current = candidate, candidate_metrics
            actions.append("lightly_sharpened")

    after = image_metrics(image)
    prefer_jpeg = mime_type.lower() in {"image/jpeg", "image/jpg"} or original_format == "JPEG"
    encoded, output_mime = _encode(image, prefer_jpeg)
    report = {
        "version": 1,
        "used_original": not actions,
        "actions": actions,
        "crop_confidence": crop_confidence or None,
        "deskew_score_improvement": round(deskew_improvement, 3) if angle else None,
        "original_size": list(original_size),
        "ocr_size": list(image.size),
        "quality_before": asdict(before),
        "quality_after": asdict(after),
    }
    return PreprocessedImage(encoded, output_mime, report)
