from legal_rag.config import settings
from legal_rag.infrastructure.http import request_json


class EmbeddingClient:
    def metadata(self):
        config = settings()
        return request_json(
            "embedding",
            config.embedding_url.rstrip("/") + "/health",
            headers={"Authorization": "Bearer " + config.embedding_api_key.get_secret_value()},
            method="GET",
        )

    def call(self, path, body, timeout=180):
        config = settings()
        return request_json(
            "embedding",
            config.embedding_url.rstrip("/") + path,
            headers={"Authorization": "Bearer " + config.embedding_api_key.get_secret_value()},
            body=body,
            timeout=timeout,
        )

    def query(self, text):
        return self.call("/queries", {"texts": [text]})

    def prepare(self, title, text):
        return self.call(
            "/prepare", {"title": title, "text": text}, timeout=settings().embedding_document_timeout_seconds
        )
