# app.py

import os
import sqlite3
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from google import genai
from pydantic import BaseModel

from cloud_storage.domain.entities import R2HttpConfig
from cloud_storage.infrastructure.adapter import HTTPR2Adapter
from core.invoice_service import InvoiceService, NotAnInvoiceError
from invoice_storage.domain.entities import Invoice
from invoice_storage.infrastructure.sqlite_invoice_repository import (
    SQLiteInvoiceRepository,
)
from invoice_storage.infrastructure.turso_invoice_repository import (
    TursoInvoiceRepository,
)
from vision.infrastructure.gemini_vision_adapter import GeminiVisionAdapter


class LineItemResponse(BaseModel):
    quantity: int
    description: str
    unit_price: float
    amount: float


class InvoiceResponse(BaseModel):
    invoice_number: str
    vendor_name: str
    date: str
    total_amount: float
    line_items: list[LineItemResponse]
    image_url: str | None = None

    @classmethod
    def from_entity(cls, entity: Invoice) -> "InvoiceResponse":
        return cls(
            invoice_number=entity.invoice_number,
            vendor_name=entity.vendor_name,
            date=entity.date.isoformat(),
            total_amount=float(entity.total_amount),
            line_items=[
                LineItemResponse(
                    quantity=item.quantity,
                    description=item.description,
                    unit_price=float(item.unit_price),
                    amount=float(item.amount),
                )
                for item in entity.line_items
            ],
            image_url=entity.image_url,
        )


app_state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    load_dotenv()
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is missing.")

    client = genai.Client(api_key=api_key)
    vision_adapter = GeminiVisionAdapter(client=client)

    conn = sqlite3.connect("invoices.db", check_same_thread=False)
    db_url = "libsql://invoicedb-nagieight.aws-ap-northeast-1.turso.io"

    turso_token = os.environ.get("TURSO_TOKEN")
    if not turso_token or not db_url:
        raise ValueError("TURSO_DATABASE_URL and TURSO_AUTH_TOKEN must both be provided.")
    repo = TursoInvoiceRepository(database_url=db_url, auth_token=turso_token)
    # repo = SQLiteInvoiceRepository(conn)
    r2_config = R2HttpConfig(
        account_id=os.environ["R2_ACCOUNT_ID"],
        bucket_name=os.environ["R2_BUCKET_NAME"],
        access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    )
    r2_public_domain = os.environ["R2_PUBLIC_DOMAIN"]
    r2_adapter = HTTPR2Adapter(config=r2_config)
    app_state["invoice_service"] = InvoiceService(
        vision_adapter=vision_adapter,
        storage_port=repo,
        r2_adapter=r2_adapter,
        r2_public_domain=r2_public_domain,
    )
    app_state["repo"] = repo
    app_state["conn"] = conn

    yield

    conn.close()


app = FastAPI(title="Invoice Extractor API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post(
    "/api/invoices/extract",
    response_model=InvoiceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def extract_invoice(file: UploadFile = File(...)) -> InvoiceResponse:
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty."
        )

    invoice_service: InvoiceService = app_state["invoice_service"]

    try:
        invoice = await invoice_service.extract_invoice(file_bytes)
    except NotAnInvoiceError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        )

    return InvoiceResponse.from_entity(invoice)


@app.get("/api/invoices", response_model=list[InvoiceResponse])
async def list_invoices() -> list[InvoiceResponse]:
    repo: SQLiteInvoiceRepository = app_state["repo"]
    entities = repo.list_all()
    return [InvoiceResponse.from_entity(inv) for inv in entities]


@app.get("/api/invoices/{invoice_number}", response_model=InvoiceResponse)
async def get_invoice(invoice_number: str) -> InvoiceResponse:
    repo: SQLiteInvoiceRepository = app_state["repo"]
    invoice = repo.get_by_number(invoice_number)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    return InvoiceResponse.from_entity(invoice)

app.mount("/", StaticFiles(directory="src/presentation", html=True), name="static")
