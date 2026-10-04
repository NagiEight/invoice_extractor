import json
import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from cloud_storage.domain.ports import R2AdapterPort
from invoice_storage.domain.entities import Invoice, InvoiceLineItem
from invoice_storage.domain.ports import InvoiceRepository
from vision.domain.ports import VisionPort


class NotAnInvoiceError(Exception):
    """Raised when the vision model determines the document is not an invoice."""

    pass


def _clean_decimal_str(val: Any) -> str:
    if isinstance(val, (int, float)):
        return str(val)
    return re.sub(r"[^\d.-]", "", str(val))


def map_json_to_invoice(
    raw_text: str | dict[str, Any], image_url: str | None = None
) -> Invoice:
    if isinstance(raw_text, dict):
        data = raw_text
    else:
        cleaned_text = re.sub(
            r"^```(?:json)?\s*|\s*```$", "", raw_text.strip(), flags=re.IGNORECASE
        )
        data = json.loads(cleaned_text)

    # Reject non-invoice images immediately
    if not data.get("is_invoice", True):
        raise NotAnInvoiceError(
            data.get("reason", "The provided document is not an invoice.")
        )

    return Invoice(
        invoice_number=data["invoice_number"],
        vendor_name=data["vendor_name"],
        date=datetime.strptime(data["date"], "%m/%d/%Y").date(),
        total_amount=Decimal(_clean_decimal_str(data["total_amount"])),
        line_items=[
            InvoiceLineItem(
                quantity=int(
                    item.get("qty")
                    if item.get("qty") is not None
                    else item.get("quantity", 0)
                ),
                description=item["description"],
                unit_price=Decimal(_clean_decimal_str(item["unit_price"])),
                amount=Decimal(_clean_decimal_str(item["amount"])),
            )
            for item in data.get("line_items", [])
        ],
        image_url=image_url,
    )


class InvoiceService:

    def __init__(
        self,
        vision_adapter: VisionPort,
        storage_port: InvoiceRepository,
        r2_adapter: R2AdapterPort,
        r2_public_domain: str,
    ) -> None:
        self._vision_adapter = vision_adapter
        self._storage_port = storage_port
        self._r2_adapter = r2_adapter
        self._r2_public_domain = r2_public_domain.rstrip("/")

    def _detect_mime_type(self, document_bytes: bytes) -> tuple[str, str]:
        if document_bytes.startswith(b"%PDF"):
            return "application/pdf", ".pdf"
        elif document_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png", ".png"
        elif document_bytes.startswith(b"\xff\xd8\xff"):
            return "image/jpeg", ".jpg"
        elif (
            document_bytes.startswith(b"RIFF")
            and document_bytes[8:12] == b"WEBP"
        ):
            return "image/webp", ".webp"

        return "application/octet-stream", ".bin"

    async def extract_invoice(self, img: bytes) -> Invoice:
        mime_type, ext = self._detect_mime_type(img)

        prompt = (
            "Analyze the image and determine if it is an invoice or receipt.\n"
            "If it IS an invoice, set 'is_invoice' to true and fill out all fields.\n"
            "If it is NOT an invoice (e.g., family picture, meme, general photo, random document), "
            "set 'is_invoice' to false and provide a short 'reason'.\n\n"
            "Return a strict raw JSON object using this exact structure:\n"
            "{\n"
            '  "is_invoice": true,\n'
            '  "reason": "optional rejection reason string",\n'
            '  "invoice_number": "string",\n'
            '  "vendor_name": "string",\n'
            '  "date": "MM/DD/YYYY",\n'
            '  "total_amount": "string",\n'
            '  "line_items": [\n'
            "    {\n"
            '      "quantity": 1,\n'
            '      "description": "string",\n'
            '      "unit_price": "string",\n'
            '      "amount": "string"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "Do not include markdown code block syntax (like ```json). Return ONLY raw JSON."
        )

        # 1. Ask Gemini Vision to extract and classify first
        raw_result = self._vision_adapter.extract_text(
            document_bytes=img, mime_type=mime_type, prompt=prompt
        )

        # 2. Parse JSON & check if it's an invoice (raises NotAnInvoiceError if false)
        # Pass image_url=None to validate schema without mutating later
        map_json_to_invoice(raw_result, image_url=None)

        # 3. Upload to Cloudflare R2 AFTER confirming it's an invoice
        key = f"{uuid.uuid4()}{ext}"
        await self._r2_adapter.upload(
            key=key, data=img, content_type=mime_type
        )
        image_url = f"{self._r2_public_domain}/{key}"

        # 4. Create the final immutable Invoice entity with the image_url included
        invoice = map_json_to_invoice(raw_result, image_url=image_url)

        self._storage_port.save(invoice)
        return invoice
