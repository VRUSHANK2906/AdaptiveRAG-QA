import os
import re
from typing import Dict, List, Tuple

import faiss
import fitz  # PyMuPDF
import numpy as np
import requests
from sentence_transformers import SentenceTransformer


class AdaptiveRAG:
    """
    Adaptive RAG pipeline:
      1. Extract PDF text and chunk it.
      2. Embed chunks and store them in a FAISS cosine-similarity index.
      3. Classify the query by complexity.
      4. Adapt top-k retrieval to the query type.
      5. If retrieval confidence is weak, expand the query and retry.
      6. Generate a grounded answer with source-page references.
    """

    def __init__(
        self,
        embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        ollama_model: str | None = None,
        ollama_url: str = "http://localhost:11434/api/generate",
    ):
        self.embedder = SentenceTransformer(embedding_model)
        self.ollama_model = ollama_model or os.getenv("OLLAMA_MODEL", "gemma3:1b")
        self.ollama_url = ollama_url
        self.index = None
        self.chunks: List[Dict] = []

    # ---------------------------
    # Document processing
    # ---------------------------
    @staticmethod
    def _clean_text(text: str) -> str:
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 170, overlap: int = 35) -> List[str]:
        words = text.split()
        if not words:
            return []

        chunks = []
        start = 0
        while start < len(words):
            end = min(start + chunk_size, len(words))
            chunk = " ".join(words[start:end]).strip()
            if chunk:
                chunks.append(chunk)
            if end == len(words):
                break
            start = max(0, end - overlap)
        return chunks

    def ingest_pdfs(self, pdf_paths: List[str]) -> int:
        self.chunks = []

        for pdf_path in pdf_paths:
            filename = os.path.basename(pdf_path)
            doc = fitz.open(pdf_path)

            for page_number, page in enumerate(doc, start=1):
                page_text = self._clean_text(page.get_text("text"))
                for local_id, chunk_text in enumerate(self._chunk_text(page_text)):
                    self.chunks.append(
                        {
                            "id": len(self.chunks),
                            "text": chunk_text,
                            "source": filename,
                            "page": page_number,
                            "local_chunk": local_id,
                        }
                    )
            doc.close()

        if not self.chunks:
            raise ValueError("No readable text was found in the uploaded PDF files.")

        texts = [c["text"] for c in self.chunks]
        embeddings = self.embedder.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        ).astype("float32")

        dimension = embeddings.shape[1]
        self.index = faiss.IndexFlatIP(dimension)
        self.index.add(embeddings)
        return len(self.chunks)

    # ---------------------------
    # Adaptive routing
    # ---------------------------
    @staticmethod
    def classify_query(query: str) -> Dict:
        """Fast rule-based router so routing does not require another model call."""
        q = query.lower().strip()
        words = q.split()

        complex_markers = [
            "compare", "difference", "differentiate", "relationship", "relate",
            "why", "how", "analyze", "analyse", "advantages", "disadvantages",
            "cause", "effect", "impact", "explain", "summarize", "summarise",
            "across", "between", "based on", "evaluate", "justify",
        ]
        multi_hop_markers = [
            "compare", "between", "relationship", "cause and effect",
            "advantages and disadvantages", "how does", "why does",
        ]

        if any(marker in q for marker in multi_hop_markers) or len(words) >= 22:
            return {
                "route": "complex/multi-hop",
                "top_k": 8,
                "confidence_threshold": 0.24,
            }

        if any(marker in q for marker in complex_markers) or len(words) >= 11:
            return {
                "route": "explanatory",
                "top_k": 5,
                "confidence_threshold": 0.27,
            }

        return {
            "route": "simple/factual",
            "top_k": 3,
            "confidence_threshold": 0.30,
        }

    # ---------------------------
    # Retrieval
    # ---------------------------
    def _retrieve(self, query: str, top_k: int) -> List[Dict]:
        if self.index is None or not self.chunks:
            raise ValueError("Please upload and index at least one PDF first.")

        query_vector = self.embedder.encode(
            [query],
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        ).astype("float32")

        k = min(top_k, len(self.chunks))
        scores, indices = self.index.search(query_vector, k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            chunk = dict(self.chunks[int(idx)])
            chunk["score"] = float(score)
            results.append(chunk)
        return results

    def _ollama_generate(self, prompt: str, temperature: float = 0.1) -> str:
        payload = {
            "model": self.ollama_model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_ctx": 8192,
            },
        }
        response = requests.post(self.ollama_url, json=payload, timeout=180)
        response.raise_for_status()
        return response.json().get("response", "").strip()

    def _expand_query(self, query: str) -> List[str]:
        prompt = f"""
Rewrite the user's question into 3 short semantic-search queries.
Keep the original meaning. Return one query per line and no explanation.

Question: {query}
""".strip()

        try:
            output = self._ollama_generate(prompt, temperature=0.0)
            variants = []
            for line in output.splitlines():
                line = re.sub(r"^[\s\-*\d.]+", "", line).strip()
                if line and line.lower() != query.lower():
                    variants.append(line)
            return [query] + variants[:3]
        except Exception:
            # The whole RAG system can still work if query expansion fails.
            return [query]

    @staticmethod
    def _merge_results(result_sets: List[List[Dict]], limit: int) -> List[Dict]:
        by_id: Dict[int, Dict] = {}
        for result_set in result_sets:
            for item in result_set:
                item_id = item["id"]
                if item_id not in by_id or item["score"] > by_id[item_id]["score"]:
                    by_id[item_id] = item

        merged = sorted(by_id.values(), key=lambda x: x["score"], reverse=True)
        return merged[:limit]

    def adaptive_retrieve(self, query: str) -> Tuple[Dict, List[Dict], bool]:
        route = self.classify_query(query)
        results = self._retrieve(query, route["top_k"])

        best_score = results[0]["score"] if results else -1.0
        second_pass_used = False

        # Adaptive fallback: weak semantic match -> query expansion + broader retrieval.
        if best_score < route["confidence_threshold"]:
            second_pass_used = True
            variants = self._expand_query(query)
            broader_k = min(max(route["top_k"] + 3, 6), 10)
            result_sets = [self._retrieve(v, broader_k) for v in variants]
            results = self._merge_results(result_sets, broader_k)

        return route, results, second_pass_used

    # ---------------------------
    # Answer generation
    # ---------------------------
    def answer(self, query: str) -> Dict:
        route, results, second_pass_used = self.adaptive_retrieve(query)

        if not results:
            return {
                "answer": "I could not find relevant information in the uploaded documents.",
                "route": route["route"],
                "confidence": 0.0,
                "second_pass": second_pass_used,
                "sources": [],
            }

        best_score = results[0]["score"]

        # Reject very weak matches instead of hallucinating.
        if best_score < 0.15:
            return {
                "answer": "The uploaded documents do not appear to contain enough relevant information to answer this question reliably.",
                "route": route["route"],
                "confidence": round(best_score, 3),
                "second_pass": second_pass_used,
                "sources": [],
            }

        context_parts = []
        for i, item in enumerate(results, start=1):
            context_parts.append(
                f"[Source {i} | {item['source']} | page {item['page']}]\n{item['text']}"
            )
        context = "\n\n".join(context_parts)

        prompt = f"""
You are a document question-answering assistant.
Answer ONLY from the supplied context.
If the context is insufficient, clearly say that the documents do not provide enough information.
Do not invent facts.
Give a clear, concise answer.
After important claims, cite the supporting source in square brackets, for example [Source 2].

QUESTION:
{query}

CONTEXT:
{context}

ANSWER:
""".strip()

        try:
            generated_answer = self._ollama_generate(prompt, temperature=0.1)
        except requests.RequestException as exc:
            generated_answer = (
                "The relevant document passages were retrieved, but the local Ollama model "
                f"could not be reached. Start Ollama and try again. Error: {exc}"
            )

        sources = []
        seen = set()
        for item in results:
            key = (item["source"], item["page"])
            if key not in seen:
                seen.add(key)
                sources.append(
                    {
                        "file": item["source"],
                        "page": item["page"],
                        "score": round(item["score"], 3),
                    }
                )

        return {
            "answer": generated_answer,
            "route": route["route"],
            "confidence": round(best_score, 3),
            "second_pass": second_pass_used,
            "retrieved_chunks": len(results),
            "sources": sources,
        }
