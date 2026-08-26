import cohere
from api.core.config import settings
from api.core.constants import EMBED_MODEL

_client = None


def get_client() -> cohere.Client:
    global _client
    if _client is None:
        _client = cohere.Client(settings.cohere_api_key)
    return _client


def embed_text(text: str) -> list[float]:
    response = get_client().embed(
        texts=[text],
        model=EMBED_MODEL,
        input_type="search_query"
    )
    return list(response.embeddings[0])


def embed_batch(texts: list[str]) -> list[list[float]]:
    response = get_client().embed(
        texts=texts,
        model=EMBED_MODEL,
        input_type="search_document"
    )
    return [list(e) for e in response.embeddings]
