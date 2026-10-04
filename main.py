import asyncio
import os
import sqlite3

from dotenv import load_dotenv
from google import genai

from cloud_storage.domain.entities import R2HttpConfig
from cloud_storage.infrastructure.adapter import HTTPR2Adapter
from core.invoice_service import InvoiceService
from invoice_storage.infrastructure.sqlite_invoice_repository import (
    SQLiteInvoiceRepository,
)
from vision.infrastructure.gemini_vision_adapter import GeminiVisionAdapter


async def main() -> None:
    load_dotenv()
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is missing.")

    api_key = os.environ["GEMINI_API_KEY"]
    db_path = os.environ.get("DATABASE_PATH", "/app/data/invoices.db")
    conn = sqlite3.connect(db_path, check_same_thread=False)

    r2_config = R2HttpConfig(
        account_id=os.environ["R2_ACCOUNT_ID"],
        bucket_name=os.environ["R2_BUCKET_NAME"],
        access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    )
    r2_public_domain = os.environ["R2_PUBLIC_DOMAIN"]

    # 2. Instantiate adapters & services
    client = genai.Client(api_key=api_key)
    vision_adapter = GeminiVisionAdapter(client=client)

    conn = sqlite3.connect(db_path)
    repo = SQLiteInvoiceRepository(conn)

    r2_adapter = HTTPR2Adapter(config=r2_config)

    invoice_service = InvoiceService(
        vision_adapter=vision_adapter,
        storage_port=repo,
        r2_adapter=r2_adapter,
        r2_public_domain=r2_public_domain,
    )

    # Replace with path to your sample invoice image/pdf
    file_path = "data/photo_2026-10-04_15-13-56.jpg"

    if not os.path.exists(file_path):
        print(f"Please place a test file at '{file_path}' to run.")
        return

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    invoice = await invoice_service.extract_invoice(file_bytes)
    print("--- Extracted Invoice Data ---")
    print(invoice)


if __name__ == "__main__":
    asyncio.run(main())
