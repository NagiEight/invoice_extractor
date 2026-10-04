# invoice_storage/infrastructure/repositories/sqlite_invoice_repository.py

import sqlite3
from datetime import date
from decimal import Decimal

from invoice_storage.domain.entities import Invoice, InvoiceLineItem
from invoice_storage.domain.ports import (
    InvoiceRepository,
    InvoiceSortField,
    SortDirection,
)


class SQLiteInvoiceRepository(InvoiceRepository):

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._init_db()

    def _init_db(self) -> None:
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS invoices (
                    invoice_number TEXT PRIMARY KEY,
                    vendor_name TEXT NOT NULL,
                    date TEXT NOT NULL,
                    total_amount TEXT NOT NULL,
                    image_url TEXT
                );

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
            # Safe migration for existing SQLite databases created prior to image_url
            cursor = self._conn.cursor()
            cursor.execute("PRAGMA table_info(invoices);")
            columns = [column[1] for column in cursor.fetchall()]
            if "image_url" not in columns:
                cursor.execute("ALTER TABLE invoices ADD COLUMN image_url TEXT;")

    def save(self, invoice: Invoice) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO invoices (invoice_number, vendor_name, date, total_amount, image_url)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    invoice.invoice_number,
                    invoice.vendor_name,
                    invoice.date.isoformat(),
                    str(invoice.total_amount),
                    invoice.image_url,
                ),
            )
            self._conn.execute(
                "DELETE FROM invoice_line_items WHERE invoice_number = ?",
                (invoice.invoice_number,),
            )
            self._conn.executemany(
                """
                INSERT INTO invoice_line_items (invoice_number, quantity, description, unit_price, amount)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        invoice.invoice_number,
                        item.quantity,
                        item.description,
                        str(item.unit_price),
                        str(item.amount),
                    )
                    for item in invoice.line_items
                ],
            )

    def get_by_number(self, invoice_number: str) -> Invoice | None:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT invoice_number, vendor_name, date, total_amount, image_url
            FROM invoices
            WHERE invoice_number = ?
            """,
            (invoice_number,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        line_items = self._fetch_line_items_for_invoices([invoice_number])
        return self._row_to_invoice(row, line_items.get(invoice_number, []))

    def find_by_item_description(self, item_description: str) -> list[Invoice]:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT DISTINCT i.invoice_number, i.vendor_name, i.date, i.total_amount, i.image_url
            FROM invoices i
            JOIN invoice_line_items li ON i.invoice_number = li.invoice_number
            WHERE li.description LIKE ? ESCAPE '\\'
            ORDER BY i.date DESC
            """,
            (f"%{self._escape_like(item_description)}%",),
        )
        rows = cursor.fetchall()
        if not rows:
            return []

        invoice_numbers = [row[0] for row in rows]
        line_items_map = self._fetch_line_items_for_invoices(invoice_numbers)
        return [
            self._row_to_invoice(row, line_items_map.get(row[0], []))
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
        params: list[int] = []

        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        elif offset > 0:
            query += " LIMIT -1 OFFSET ?"
            params.append(offset)

        cursor = self._conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        if not rows:
            return []

        invoice_numbers = [row[0] for row in rows]
        line_items_map = self._fetch_line_items_for_invoices(invoice_numbers)
        return [
            self._row_to_invoice(row, line_items_map.get(row[0], []))
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
        with self._conn:
            self._conn.execute(
                "DELETE FROM invoices WHERE invoice_number = ?",
                (invoice_number,),
            )

    def _fetch_line_items_for_invoices(
        self, invoice_numbers: list[str]
    ) -> dict[str, list[InvoiceLineItem]]:
        if not invoice_numbers:
            return {}

        placeholders = ",".join("?" for _ in invoice_numbers)
        cursor = self._conn.cursor()
        cursor.execute(
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
        for inv_num, qty, desc, unit_price, amount in cursor.fetchall():
            items_map[inv_num].append(
                InvoiceLineItem(
                    quantity=qty,
                    description=desc,
                    unit_price=Decimal(unit_price),
                    amount=Decimal(amount),
                )
            )

        return items_map

    @staticmethod
    def _row_to_invoice(
        row: tuple[str, str, str, str, str | None], line_items: list[InvoiceLineItem]
    ) -> Invoice:
        return Invoice(
            invoice_number=row[0],
            vendor_name=row[1],
            date=date.fromisoformat(row[2]),
            total_amount=Decimal(row[3]),
            line_items=line_items,
            image_url=row[4],
        )

    @staticmethod
    def _escape_like(text: str) -> str:
        return (
            text.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
