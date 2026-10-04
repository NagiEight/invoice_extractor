from typing import Protocol


class VisionPort(Protocol):
    def extract_text(self, document_bytes: bytes,mime_type:str, prompt: str) -> str:
        ...
