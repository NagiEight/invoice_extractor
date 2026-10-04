from google import genai
from google.genai import types

from vision.domain.ports import VisionPort


class GeminiVisionAdapter(VisionPort):
    def __init__(self, client: genai.Client, model: str = "gemini-2.5-flash") -> None:
        self._client = client
        self._model = model

    def extract_text(self, document_bytes: bytes,mime_type:str, prompt: str) -> str:
        part = types.Part.from_bytes(
            data=document_bytes,
            mime_type=mime_type,
        )

        prompt = f"{prompt}"

        response = self._client.models.generate_content(
            model=self._model, contents=[part, prompt]
        )

        return response.text or ""
