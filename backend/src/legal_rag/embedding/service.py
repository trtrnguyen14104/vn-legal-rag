import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from legal_rag.config import settings
from legal_rag.domain.text import legal_units, normalize
from legal_rag.embedding.chunking import chunk_unit
from legal_rag.embedding.encoder import Encoder


class Texts(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=64)


class DocumentInput(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=1_000_000)


def authorize(authorization: str = Header(default="")):
    expected = "Bearer " + settings().embedding_api_key.get_secret_value()
    if not secrets.compare_digest(authorization, expected):
        raise HTTPException(401, "Unauthorized")


@asynccontextmanager
async def lifespan(app):
    app.state.encoder = Encoder()
    yield


app = FastAPI(title="Internal E5 / VnCoreNLP", lifespan=lifespan, dependencies=[Depends(authorize)])


@app.get("/health")
def health():
    return {"status": "ready", "model": settings().embedding_model, "revision": app.state.encoder.revision}


@app.post("/queries")
def queries(body: Texts):
    encoder = app.state.encoder
    with encoder.lock:
        normalized = [normalize(t) for t in body.texts]
        texts = ["query: " + t for t in normalized]
        if any(not t or encoder.count("query: " + t) > 512 for t in normalized):
            raise HTTPException(422, "Query exceeds E5 token budget; shorten the question")
        return {
            "vectors": encoder.encode(texts),
            "revision": encoder.revision,
            "segmented": [" ".join(encoder.nlp.word_segment(t)) for t in normalized],
        }


@app.post("/prepare")
def prepare(body: DocumentInput):
    encoder = app.state.encoder
    with encoder.lock:
        units = legal_units(body.text)
        chunks = []
        for ordinal, unit in enumerate(units):
            parts = chunk_unit(unit["raw_text"], body.title, unit["locator"], encoder.count)
            for index, part in enumerate(parts):
                part.update(
                    unit_ordinal=ordinal,
                    ordinal=index,
                    segmented_text=" ".join(encoder.nlp.word_segment(part["normalized_text"])),
                )
                chunks.append(part)
        if not chunks:
            raise HTTPException(422, "No legal content")
        vectors = encoder.encode([c["embedding_text"] for c in chunks])
        for chunk, vector in zip(chunks, vectors, strict=True):
            chunk["embedding"] = vector
            chunk.pop("embedding_text")
        return {"units": units, "chunks": chunks, "revision": encoder.revision}
