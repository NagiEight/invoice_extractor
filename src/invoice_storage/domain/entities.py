from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class InvoiceLineItem:
    quantity: int
    description: str
    unit_price: Decimal
    amount: Decimal


@dataclass(frozen=True)
class Invoice:
    invoice_number: str
    vendor_name: str
    date: date
    total_amount: Decimal
    line_items: list[InvoiceLineItem]
    image_url: str | None = None
