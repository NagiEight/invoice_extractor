# invoice_storage/infrastructure/repositories/turso_invoice_repository.py

import json
import urllib.request
from datetime import date
from decimal import Decimal
from typing import Any

from invoice_storage.domain.entities import Invoice, InvoiceLineItem
from invoice_storage.domain.ports import (
    InvoiceRepository,
    InvoiceSortField,
    SortDirection,
)


class TursoInvoiceRepository(InvoiceRepository):

    def __init__(self, database_url: str, auth_token: str) -> None:
        clean_url = database_url.replace("libsql://", "https://")
        if not clean_url.startswith("https://"):
            clean_url = f"https://{clean_url}"

        self._url = f"{clean_url.rstrip('/')}/v2/pipeline"
        self._auth_header = f"Bearer {auth_token}"
        self._init_db()

    def _execute_pipeline(self, requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        payload = {"requests": requests}
        req = urllib.request.Request(
            self._url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": self._auth_header,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("results", [])

    def _execute_stmt(self, sql: str, args: list[Any] | None = None) -> list[tuple[Any, ...]]:
        formatted_args = []
        for arg in args or []:
            if arg is None:
                formatted_args.append({"type": "null"})
            elif isinstance(arg, int):
                formatted_args.append({"type": "integer", "value": str(arg)})
            elif isinstance(arg, float):
                formatted_args.append({"type": "float", "value": arg})
            else:
                formatted_args.append({"type": "text", "value": str(arg)})

        request_body = {
            "type": "execute",
            "stmt": {
                "sql": sql,
                "args": formatted_args,
            },
        }

        results = self._execute_pipeline([request_body, {"type": "close"}])
        if not results:
            return []

        first_res = results[0]
        if first_res.get("type") == "error":
            raise RuntimeError(f"Turso API Error: {first_res.get('error')}")

        result_data = first_res.get("response", {}).get("result", {})
        rows_raw = result_data.get("rows", [])

        parsed_rows: list[tuple[Any, ...]] = []
        for row in rows_raw:
            parsed_row = tuple(cell.get("value") for cell in row)
            parsed_rows.append(parsed_row)

        return parsed_rows

    def _init_db(self) -> None:
        self._execute_stmt(
            """
            CREATE TABLE IF NOT EXISTS invoices (
                invoice_number TEXT PRIMARY KEY,
                vendor_name TEXT NOT NULL,
                date TEXT NOT NULL,
                total_amount TEXT NOT NULL,
                image_url TEXT
            );
            """
        )
        self._execute_stmt(
            """
            CREATE TABLE IF NOT EXISTS invoice_line_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_number TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                description TEXT NOT NULL,
                unit_price TEXT NOT NULL,
                amount TEXT NOT NULL,
                FOREIGN KEY (invoice_number) REFERENCES invoices (invoice_number) ON DELETE CASCADE
            );
            """
        )

        columns_rows = self._execute_stmt("PRAGMA table_info(invoices);")
        columns = [row[1] for row in columns_rows if len(row) > 1]
        if "image_url" not in columns:
            self._execute_stmt("ALTER TABLE invoices ADD COLUMN image_url TEXT;")

    def save(self, invoice: Invoice) -> None:
        save_requests: list[dict[str, Any]] = [
            {
                "type": "execute",
                "stmt": {
                    "sql": """
                        INSERT OR REPLACE INTO invoices (invoice_number, vendor_name, date, total_amount, image_url)
                        VALUES (?, ?, ?, ?, ?)
                    """,
                    "args": [
                        {"type": "text", "value": invoice.invoice_number},
                        {"type": "text", "value": invoice.vendor_name},
                        {"type": "text", "value": invoice.date.isoformat()},
                        {"type": "text", "value": str(invoice.total_amount)},
                        {"type": "text", "value": invoice.image_url} if invoice.image_url else {"type": "null"},
                    ],
                },
            },
            {
                "type": "execute",
                "stmt": {
                    "sql": "DELETE FROM invoice_line_items WHERE invoice_number = ?",
                    "args": [{"type": "text", "value": invoice.invoice_number}],
                },
            },
        ]

        for item in invoice.line_items:
            save_requests.append(
                {
                    "type": "execute",
                    "stmt": {
                        "sql": """
                            INSERT INTO invoice_line_items (invoice_number, quantity, description, unit_price, amount)
                            VALUES (?, ?, ?, ?, ?)
                        """,
                        "args": [
                            {"type": "text", "value": invoice.invoice_number},
                            {"type": "integer", "value": str(item.quantity)},
                            {"type": "text", "value": item.description},
                            {"type": "text", "value": str(item.unit_price)},
                            {"type": "text", "value": str(item.amount)},
                        ],
                    },
                }
            )

        save_requests.append({"type": "close"})
        self._execute_pipeline(save_requests)

    def get_by_number(self, invoice_number: str) -> Invoice | None:
        rows = self._execute_stmt(
            """
            SELECT invoice_number, vendor_name, date, total_amount, image_url
            FROM invoices
            WHERE invoice_number = ?
            """,
            [invoice_number],
        )
        if not rows:
            return None

        line_items = self._fetch_line_items_for_invoices([invoice_number])
        return self._row_to_invoice(rows[0], line_items.get(invoice_number, []))

    def find_by_item_description(self, item_description: str) -> list[Invoice]:
        rows = self._execute_stmt(
            """
            SELECT DISTINCT i.invoice_number, i.vendor_name, i.date, i.total_amount, i.image_url
            FROM invoices i
            JOIN invoice_line_items li ON i.invoice_number = li.invoice_number
            WHERE li.description LIKE ? ESCAPE '\\'
            ORDER BY i.date DESC
            """,
            [f"%{self._escape_like(item_description)}%"],
        )
        if not rows:
            return []

        invoice_numbers = [str(row[0]) for row in rows]
        line_items_map = self._fetch_line_items_for_invoices(invoice_numbers)
        return [
            self._row_to_invoice(row, line_items_map.get(str(row[0]), []))
            for row in rows
        ]

    def list_all(
        self,
        sort_by: InvoiceSortField = InvoiceSortField.DATE,
        direction: SortDirection = SortDirection.DESC,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Invoice]:
        sort_column_map = {
            InvoiceSortField.INVOICE_NUMBER: "invoice_number",
            InvoiceSortField.VENDOR_NAME: "vendor_name",
            InvoiceSortField.TOTAL_AMOUNT: "CAST(total_amount AS NUMERIC)",
            InvoiceSortField.DATE: "date",
        }
        order_by = sort_column_map[sort_by]
        dir_sql = "ASC" if direction == SortDirection.ASC else "DESC"

        query = f"""
            SELECT invoice_number, vendor_name, date, total_amount, image_url
            FROM invoices
            ORDER BY {order_by} {dir_sql}
        """
        params: list[Any] = []

        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        elif offset > 0:
            query += " LIMIT -1 OFFSET ?"
            params.append(offset)

        rows = self._execute_stmt(query, params)
        if not rows:
            return []

        invoice_numbers = [str(row[0]) for row in rows]
        line_items_map = self._fetch_line_items_for_invoices(invoice_numbers)
        return [
            self._row_to_invoice(row, line_items_map.get(str(row[0]), []))
            for row in rows
        ]

    def get_ranked_by_amount(
        self,
        limit: int = 10,
        direction: SortDirection = SortDirection.DESC,
    ) -> list[Invoice]:
        return self.list_all(
            sort_by=InvoiceSortField.TOTAL_AMOUNT,
            direction=direction,
            limit=limit,
            offset=0,
        )

    def delete_by_number(self, invoice_number: str) -> None:
        self._execute_stmt(
            "DELETE FROM invoices WHERE invoice_number = ?",
            [invoice_number],
        )

    def _fetch_line_items_for_invoices(
        self, invoice_numbers: list[str]
    ) -> dict[str, list[InvoiceLineItem]]:
        if not invoice_numbers:
            return {}

        placeholders = ",".join("?" for _ in invoice_numbers)
        rows = self._execute_stmt(
            f"""
            SELECT invoice_number, quantity, description, unit_price, amount
            FROM invoice_line_items
            WHERE invoice_number IN ({placeholders})
            ORDER BY id ASC
            """,
            invoice_numbers,
        )

        items_map: dict[str, list[InvoiceLineItem]] = {
            num: [] for num in invoice_numbers
        }
        for inv_num, qty, desc, unit_price, amount in rows:
            inv_str = str(inv_num)
            items_map[inv_str].append(
                InvoiceLineItem(
                    quantity=int(qty),
                    description=str(desc),
                    unit_price=Decimal(str(unit_price)),
                    amount=Decimal(str(amount)),
                )
            )

        return items_map

    @staticmethod
    def _row_to_invoice(
        row: tuple[Any, ...], line_items: list[InvoiceLineItem]
    ) -> Invoice:
        return Invoice(
            invoice_number=str(row[0]),
            vendor_name=str(row[1]),
            date=date.fromisoformat(str(row[2])),
            total_amount=Decimal(str(row[3])),
            line_items=line_items,
            image_url=str(row[4]) if row[4] is not None else None,
        )

    @staticmethod
    def _escape_like(text: str) -> str:
        return (
            text.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
