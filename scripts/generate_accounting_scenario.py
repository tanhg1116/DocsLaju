"""Generate the synthetic Laju Office Solutions acceptance documents.

The fixtures contain no real personal or company information and make no API
calls. Run from the repository root with the same Python environment as Flask.
"""

from __future__ import annotations

import io
import textwrap
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tests" / "scenarios" / "laju_office_solutions" / "documents"
COMPANY = "Laju Office Solutions Sdn. Bhd."


def _pdf(filename: str, title: str, lines: list[str], *, pages: int = 1) -> None:
    document = pymupdf.open()
    for page_number in range(1, pages + 1):
        page = document.new_page(width=595, height=842)
        page.insert_text((48, 42), title, fontsize=17, fontname="hebo", color=(0.12, 0.18, 0.28))
        page.insert_text((48, 62), f"Synthetic acceptance fixture · page {page_number} of {pages}", fontsize=8, color=(0.4, 0.45, 0.52))
        y = 92
        page_lines = lines if pages == 1 else [
            f"Statement reference: ST-FP-2026-08",
            f"Account: {COMPANY}",
            f"Statement detail page: {page_number}",
            "Date        Reference         Description                      Amount (MYR)",
            f"2026-08-{min(page_number + 1, 28):02d}  FP-{page_number:04d}          Office equipment transaction       {120 + page_number * 7:.2f}",
            "This page is intentionally part of one long supplier statement.",
        ]
        for line in page_lines:
            wrapped = textwrap.wrap(line, width=88, replace_whitespace=False) or [""]
            for part in wrapped:
                page.insert_text((48, y), part, fontsize=10.5, fontname="helv", color=(0.08, 0.1, 0.13))
                y += 16
            y += 3
        page.draw_line((48, 790), (547, 790), color=(0.75, 0.78, 0.82), width=0.7)
        page.insert_text((48, 808), "FOR TESTING ONLY · ALL DETAILS ARE FICTIONAL", fontsize=8, color=(0.5, 0.22, 0.2))
    document.save(OUTPUT / filename, garbage=4, deflate=True)
    document.close()


def _font(size: int, bold: bool = False):
    candidates = ["DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", "arialbd.ttf" if bold else "arial.ttf"]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _image_bytes(
    title: str,
    lines: list[str],
    *,
    output_format: str,
    rotate: float = 0,
    blur: float = 0,
    low_resolution: bool = False,
    quality: int = 92,
) -> bytes:
    image = Image.new("RGB", (900, 1180), "#fdfcf8")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((34, 30, 866, 1140), radius=14, outline="#c7ccd3", width=3, fill="#ffffff")
    draw.text((72, 68), title, font=_font(34, True), fill="#17202a")
    draw.text((72, 116), "Synthetic acceptance fixture", font=_font(16), fill="#6b7280")
    y = 180
    for line in lines:
        for part in textwrap.wrap(line, width=54, replace_whitespace=False) or [""]:
            draw.text((72, y), part, font=_font(22), fill="#20252b")
            y += 34
        y += 10
    draw.text((72, 1085), "FOR TESTING ONLY · ALL DETAILS ARE FICTIONAL", font=_font(14, True), fill="#9a3e3e")
    if low_resolution:
        image = image.resize((360, 472), Image.Resampling.LANCZOS)
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(blur))
    if rotate:
        image = image.rotate(rotate, expand=True, fillcolor="#e7e5df")
    stream = io.BytesIO()
    save_options = {"quality": quality, "optimize": True} if output_format == "JPEG" else {"optimize": True}
    image.save(stream, format=output_format, **save_options)
    return stream.getvalue()


def _image(filename: str, title: str, lines: list[str], **options) -> None:
    output_format = "PNG" if filename.lower().endswith(".png") else "JPEG"
    (OUTPUT / filename).write_bytes(_image_bytes(title, lines, output_format=output_format, **options))


def generate() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)

    _pdf("01_supplier_quotation_QT-FP-081.pdf", "QUOTATION QT-FP-081", [
        "Quotation from: FurniPro Sdn. Bhd. (202601000001)",
        f"Quotation for: {COMPANY} (202601012345)",
        "Quotation date: 2026-08-01    Valid until: 2026-08-15    Currency: MYR",
        "10 × Ergonomic chair @ 320.00 = 3,200.00",
        "1 × Delivery service @ 200.00 = 200.00",
        "Subtotal: 3,400.00    Discount: 100.00    Tax: 264.00",
        "TOTAL: MYR 3,564.00",
        "Payment terms: 30 days. Delivery within five business days.",
    ])
    _pdf("02_purchase_order_PO-2026-044.pdf", "PURCHASE ORDER PO-2026-044", [
        f"Buyer: {COMPANY}", "Supplier: FurniPro Sdn. Bhd.",
        "Order date: 2026-08-03    Linked quotation: QT-FP-081",
        "10 × Ergonomic chair @ MYR 320.00",
        "1 × Delivery service @ MYR 200.00",
        "Approved order total: MYR 3,564.00",
    ])
    _image("03_delivery_order_DO-2026-118.jpg", "DELIVERY ORDER", [
        "Reference: DO-2026-118", "Date: 2026-08-05", f"Delivered to: {COMPANY}",
        "Linked PO: PO-2026-044", "Ergonomic chairs delivered: 9 units",
        "Delivery service: completed", "Received by: Nur Aina",
    ], rotate=2.2)
    _pdf("04_supplier_invoice_INV-FP-338.pdf", "INVOICE INV-FP-338", [
        "Supplier: FurniPro Sdn. Bhd. (202601000001)", f"Customer: {COMPANY}",
        "Invoice date: 2026-08-05    Due date: 2026-09-04    Currency: MYR",
        "10 × Ergonomic chair @ 320.00 = 3,200.00",
        "1 × Delivery service @ 200.00 = 200.00",
        "Subtotal: 3,400.00    Discount: 100.00    Tax: 264.00",
        "TOTAL: MYR 3,564.00", "Payment terms: 30 days", "References: QT-FP-081 / PO-2026-044 / DO-2026-118",
    ])
    _pdf("05_credit_note_CN-FP-019.pdf", "CREDIT NOTE CN-FP-019", [
        "Supplier: FurniPro Sdn. Bhd.", f"Customer: {COMPANY}",
        "Date: 2026-08-12    Original invoice: INV-FP-338",
        "Returned ergonomic chair: 1 × 320.00", "Tax adjustment: 25.60", "CREDIT TOTAL: MYR 345.60",
    ])
    _image("06_payment_confirmation_PAY-0826-017.png", "PAYMENT CONFIRMATION", [
        "Reference: PAY-0826-017", "Date: 2026-08-20", "Payee: FurniPro Sdn. Bhd.",
        "Invoice: INV-FP-338", "Credit note: CN-FP-019", "Amount transferred: MYR 3,218.40", "Status: Successful",
    ])

    _pdf("10_employee_claim_EC-2026-08-014.pdf", "EMPLOYEE CLAIM EC-2026-08-014", [
        "Employee: Amir Rahman    Department: Field Services", "Submission date: 2026-08-25    Currency: MYR",
        "Petrol R-PET-8821: 120.00", "Parking R-PARK-229: 16.50", "Hotel HTL-883: 280.00",
        "Meal R-MEAL-551 claimed: 55.00", "E-hailing R-RIDE-901: 25.00", "CLAIM TOTAL: MYR 496.50",
    ])
    _image("11_receipt_petrol_R-PET-8821.jpg", "MAJU FUEL STATION", [
        "Receipt: R-PET-8821", "Date: 2026-08-11", "Fuel: MYR 120.00", "TOTAL: MYR 120.00", "Payment: Card",
    ])
    parking = _image_bytes("KL PARKING", [
        "Receipt: R-PARK-229", "Date: 2026-08-11", "Parking fee: MYR 16.50", "TOTAL: MYR 16.50",
    ], output_format="PNG")
    (OUTPUT / "12_receipt_parking_R-PARK-229.png").write_bytes(parking)
    (OUTPUT / "13_receipt_parking_exact_duplicate.png").write_bytes(parking)
    with Image.open(io.BytesIO(parking)) as parking_image:
        recompressed = io.BytesIO()
        parking_image.convert("RGB").save(recompressed, format="JPEG", quality=38, optimize=True)
    (OUTPUT / "14_receipt_parking_recompressed.jpg").write_bytes(recompressed.getvalue())
    _image("15_hotel_invoice_HTL-883.jpg", "SENTRAL HOTEL", [
        "Invoice: HTL-883", "Guest: Amir Rahman", "Date: 2026-08-18", "Room charge: MYR 280.00", "TOTAL: MYR 280.00",
    ], blur=1.5, rotate=-1.0)
    _image("16_receipt_meal_R-MEAL-551.jpg", "KEDAI MAKAN HARMONI", [
        "Receipt: R-MEAL-551", "Date: 2026-08-18", "Business meal: MYR 45.00", "TOTAL: MYR 45.00", "Payment: Cash",
    ])
    _image("17_receipt_ehailing_R-RIDE-901.jpg", "CITY RIDE", [
        "Receipt: R-RIDE-901", "Date: 2026-08-18", "Trip fare: MYR 25.00", "TOTAL: MYR 25.00",
    ], low_resolution=True, quality=70)

    _pdf("20_customer_quotation_QT-C-055.pdf", "CUSTOMER QUOTATION QT-C-055", [
        f"Supplier: {COMPANY}", "Customer: Sentosa Analytics Sdn. Bhd.",
        "Date: 2026-08-08    Valid until: 2026-08-22    Currency: MYR",
        "5 × Monitor arm @ 240.00 = 1,200.00", "Installation service: 300.00",
        "Subtotal: 1,500.00    Tax: 120.00    TOTAL: MYR 1,620.00",
    ])
    _pdf("21_customer_invoice_INV-C-204.pdf", "CUSTOMER INVOICE INV-C-204", [
        f"Supplier: {COMPANY}", "Customer: Sentosa Analytics Sdn. Bhd.",
        "Invoice date: 2026-08-15    Due date: 2026-09-14    Currency: MYR",
        "5 × Monitor arm @ 240.00 = 1,200.00", "Installation service: 300.00",
        "Subtotal: 1,500.00    Tax: 120.00    TOTAL: MYR 1,620.00", "Payment terms: 30 days",
    ])
    _pdf("22_customer_payment_advice_PA-C-88.pdf", "PAYMENT ADVICE PA-C-88", [
        "From: Sentosa Analytics Sdn. Bhd.", f"To: {COMPANY}", "Date: 2026-08-29",
        "Invoice: INV-C-204", "Partial payment: MYR 1,000.00", "Invoice balance before payment: MYR 1,620.00",
    ])

    for month, total in (("may", 820), ("june", 845), ("july", 830)):
        month_number = {"may": 5, "june": 6, "july": 7}[month]
        _pdf(f"{29 + month_number - 4:02d}_electricity_{month}_2026.pdf", f"ELECTRICITY INVOICE ELEC-2026-{month_number:02d}", [
            "Supplier: Metro Electricity", f"Customer: {COMPANY}", f"Invoice date: 2026-{month_number:02d}-28",
            f"Electricity charges: MYR {total:.2f}", f"TOTAL: MYR {total:.2f}",
        ])
    # Preserve the manifest's stable filenames.
    generated = {path.name: path for path in OUTPUT.glob("*_electricity_*_2026.pdf")}
    renames = {
        next(name for name in generated if "_may_" in name): "30_electricity_may_2026.pdf",
        next(name for name in generated if "_june_" in name): "31_electricity_june_2026.pdf",
        next(name for name in generated if "_july_" in name): "32_electricity_july_2026.pdf",
    }
    for source, target in renames.items():
        if source != target:
            generated[source].replace(OUTPUT / target)
    _pdf("33_electricity_august_anomaly.pdf", "ELECTRICITY INVOICE ELEC-2026-08", [
        "Supplier: Metro Electricity", f"Customer: {COMPANY}", "Invoice date: 2026-08-31",
        "Electricity charges: MYR 4,850.00", "TOTAL: MYR 4,850.00",
    ])
    _pdf("34_internet_august_2026.pdf", "INTERNET INVOICE NET-2026-08", [
        "Supplier: FastNet Business", f"Customer: {COMPANY}", "Invoice date: 2026-08-01",
        "Business fibre service: MYR 220.00", "TOTAL: MYR 220.00",
    ])
    _pdf("35_software_subscription_USD.pdf", "SOFTWARE INVOICE SOFT-882", [
        "Supplier: Cloud Tools Inc.", f"Customer: {COMPANY}", "Invoice date: 2026-08-02    Currency: USD",
        "Annual software subscription: USD 199.00", "TOTAL: USD 199.00", "No MYR exchange rate is printed.",
    ])
    _pdf("40_bad_arithmetic_invoice_INV-MISC-778.pdf", "INVOICE INV-MISC-778", [
        "Supplier: Maju Repairs", f"Customer: {COMPANY}", "Invoice date: 2026-08-22    Currency: MYR",
        "Repair services subtotal: 1,000.00", "Tax: 80.00", "PRINTED TOTAL: MYR 1,090.00",
    ])
    _pdf("41_missing_reference_invoice.pdf", "SUPPLIER INVOICE", [
        "Supplier: Office Pantry Trading", f"Customer: {COMPANY}", "Invoice date: 2026-08-23    Currency: MYR",
        "Pantry supplies: MYR 180.00", "TOTAL: MYR 180.00", "No invoice number appears on this document.",
    ])
    (OUTPUT / "42_damaged_supplier_attachment.pdf").write_bytes(b"%PDF-1.7\ntruncated synthetic attachment")
    _pdf("43_vendor_statement_15_pages.pdf", "FURNIPRO MONTHLY STATEMENT", [], pages=15)

    print(f"Generated {len(list(OUTPUT.iterdir()))} scenario documents in {OUTPUT}")


if __name__ == "__main__":
    generate()
