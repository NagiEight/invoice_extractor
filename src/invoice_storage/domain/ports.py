from enum import Enum
from typing import Protocol

from invoice_storage.domain.entities import Invoice


class SortDirection(Enum):
    ASC = "asc"
    DESC = "desc"


class InvoiceSortField(Enum):
    INVOICE_NUMBER = "invoice_number"
    VENDOR_NAME = "vendor_name"
    TOTAL_AMOUNT = "total_amount"
    DATE = "date"


class InvoiceRepository(Protocol):

    def save(self, invoice: Invoice) -> None:
        ...

    def get_by_number(self, invoice_number: str) -> Invoice | None:
        ...

    def find_by_item_description(self, item_description: str) -> list[Invoice]:
        ...

    def list_all(
        self,
        sort_by: InvoiceSortField = InvoiceSortField.DATE,
        direction: SortDirection = SortDirection.DESC,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Invoice]:
        ...

    def get_ranked_by_amount(
        self,
        limit: int = 10,
        direction: SortDirection = SortDirection.DESC,
    ) -> list[Invoice]:
        ...

    def delete_by_number(self, invoice_number: str) -> None:
        ...
