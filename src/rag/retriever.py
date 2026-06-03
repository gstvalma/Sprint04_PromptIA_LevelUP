"""
Retriever clínico do BluaDiagnostics — núcleo do pipeline de RAG.

Este módulo é o ponto central que orquestra as três peças da recuperação:

    documentos .md  ->  [SemanticChunker]  ->  chunks
    chunks          ->  [EmbeddingBackend] ->  vetores
    vetores+meta    ->  [ChromaDB]         ->  índice persistente e consultável

Decisões de arquitetura (Sprint 2)
----------------------------------
* Vector store = ChromaDB. Escolhido sobre FAISS porque precisamos de:
    - filtragem nativa por metadados (doc_id, versão, grau de evidência);
    - persistência local embutida (sem servidor à parte na PoC);
    - re-indexação versionada — chunks de versões antigas são ARQUIVADOS
      (flag `arquivado=true`), não deletados, atendendo ao requisito de
      auditabilidade registrado na Especificação Técnica v1.1.
  Em produção o alvo é o Weaviate (decisão da Sprint 1); a interface aqui foi
  mantida agnóstica para facilitar essa migração.

* O backend de embedding é INJETADO. Em produção usa-se o
  SentenceTransformerEmbedder; em teste, qualquer objeto que cumpra o Protocol
  EmbeddingBackend (ex.: um stub determinístico) — sem baixar modelos de GBs.

IMPORTANTE (segurança clínica): este retriever apenas RECUPERA evidência. Toda
resposta gerada a partir desses chunks passa, a jusante, pelos guardrails e pelo
human-in-the-loop definidos na Sprint 1. O RAG não substitui julgamento médico.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .chunking import Chunk, SemanticChunker
from .config import DEFAULT_CONFIG, VECTOR_STORE_DIR, RagConfig
from .embeddings import EmbeddingBackend, SentenceTransformerEmbedder


@dataclass
class ResultadoBusca:
    """Um chunk recuperado, com seu texto, metadados e score de similaridade."""

    texto: str
    metadados: Dict[str, object]
    score: float  # 1.0 = idêntico; quanto maior, mais relevante (cosseno)


class ClinicalRetriever:
    """Indexa e recupera evidência clínica sobre um índice vetorial Chroma."""

    def __init__(
        self,
        config: RagConfig = DEFAULT_CONFIG,
        embedder: Optional[EmbeddingBackend] = None,
        persist_directory: Optional[str] = None,
    ) -> None:
        """
        Args:
            config: parâmetros do RAG (modelo, chunking, top_k, métrica).
            embedder: backend de embedding. Se None, instancia o
                SentenceTransformerEmbedder padrão.
            persist_directory: pasta de persistência do Chroma. Se None, usa o
                caminho padrão data/vector_store.
        """
        self._config = config
        self._embedder: EmbeddingBackend = embedder or SentenceTransformerEmbedder(config)
        self._persist_directory = persist_directory or str(VECTOR_STORE_DIR)

        # O chunker reutiliza o MESMO contador de tokens do embedder, garantindo
        # coerência entre o limite de chunk e a janela real do modelo.
        self._chunker = SemanticChunker(
            config=config,
            contar_tokens=self._embedder.count_tokens,
        )

        self._client = None
        self._collection = None

    # ------------------------------------------------------------------ #
    # Conexão preguiçosa com o Chroma
    # ------------------------------------------------------------------ #
    @property
    def collection(self):
        """Retorna a coleção Chroma, criando cliente e coleção sob demanda."""
        if self._collection is None:
            try:
                import chromadb
                from chromadb.config import Settings
            except ImportError as exc:  # pragma: no cover - guarda de dependência
                raise ImportError(
                    "A biblioteca 'chromadb' não está instalada. Instale as "
                    "dependências do RAG com:\n    pip install -r requirements.txt"
                ) from exc

            self._client = chromadb.PersistentClient(
                path=self._persist_directory,
                settings=Settings(anonymized_telemetry=False),
            )
            # Passamos os embeddings manualmente; logo, embedding_function=None.
            self._collection = self._client.get_or_create_collection(
                name=self._config.collection_name,
                metadata={"hnsw:space": self._config.distance_metric},
            )
        return self._collection

    # ------------------------------------------------------------------ #
    # Indexação
    # ------------------------------------------------------------------ #
    def indexar_documento(
        self,
        corpo: str,
        metadados_documento: Dict[str, object],
    ) -> int:
        """Chunka, embeda e indexa um documento inteiro.

        Antes de indexar, ARQUIVA quaisquer chunks de versões anteriores do
        mesmo doc_id (em vez de deletá-los), preservando a trilha de auditoria.

        Args:
            corpo: corpo markdown do documento (sem front-matter).
            metadados_documento: metadados de proveniência; deve conter 'doc_id'.

        Returns:
            Número de chunks novos efetivamente indexados.
        """
        doc_id = str(metadados_documento.get("doc_id", "")).strip()
        if not doc_id:
            raise ValueError("metadados_documento deve conter um 'doc_id' não vazio.")

        # Versionamento: arquiva chunks ativos anteriores deste doc_id.
        self._arquivar_versoes_anteriores(doc_id)

        chunks: List[Chunk] = self._chunker.dividir(corpo, metadados_documento)
        if not chunks:
            return 0

        textos = [c.texto for c in chunks]
        vetores = self._embedder.embed_documents(textos)

        ids = [self._gerar_id_chunk(doc_id, i) for i in range(len(chunks))]
        metadados = []
        for c in chunks:
            meta = self._sanitizar_metadados(c.metadados)
            meta["arquivado"] = False  # chunk ativo
            metadados.append(meta)

        self.collection.add(
            ids=ids,
            documents=textos,
            embeddings=vetores,
            metadatas=metadados,
        )
        return len(chunks)

    def _arquivar_versoes_anteriores(self, doc_id: str) -> None:
        """Marca como arquivados os chunks ativos de um doc_id, se existirem."""
        existentes = self.collection.get(
            where={"$and": [{"doc_id": doc_id}, {"arquivado": False}]}
        )
        ids = existentes.get("ids") or []
        if not ids:
            return
        metadados = existentes.get("metadatas") or []
        for m in metadados:
            m["arquivado"] = True
        self.collection.update(ids=ids, metadatas=metadados)

    # ------------------------------------------------------------------ #
    # Busca
    # ------------------------------------------------------------------ #
    def buscar(
        self,
        consulta: str,
        top_k: Optional[int] = None,
        filtro_metadados: Optional[Dict[str, object]] = None,
        incluir_arquivados: bool = False,
    ) -> List[ResultadoBusca]:
        """Recupera os chunks mais relevantes para uma consulta.

        Args:
            consulta: pergunta/contexto clínico (em linguagem natural).
            top_k: nº de chunks a retornar (padrão: config.retrieval_top_k).
            filtro_metadados: filtro adicional do Chroma (ex.: {"doc_id": "..."}).
            incluir_arquivados: se False (padrão), ignora chunks de versões antigas.

        Returns:
            Lista de ResultadoBusca ordenada por relevância (maior score primeiro).
        """
        k = top_k or self._config.retrieval_top_k
        vetor_consulta = self._embedder.embed_query(consulta)

        condicoes: List[Dict[str, object]] = []
        if not incluir_arquivados:
            condicoes.append({"arquivado": False})
        if filtro_metadados:
            for chave, valor in filtro_metadados.items():
                condicoes.append({chave: valor})

        where: Optional[Dict[str, object]] = None
        if len(condicoes) == 1:
            where = condicoes[0]
        elif len(condicoes) > 1:
            where = {"$and": condicoes}

        resposta = self.collection.query(
            query_embeddings=[vetor_consulta],
            n_results=k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        resultados: List[ResultadoBusca] = []
        documentos = (resposta.get("documents") or [[]])[0]
        metadados = (resposta.get("metadatas") or [[]])[0]
        distancias = (resposta.get("distances") or [[]])[0]
        for texto, meta, dist in zip(documentos, metadados, distancias):
            # Chroma retorna DISTÂNCIA de cosseno (0 = idêntico). Convertendo
            # para SIMILARIDADE (score) só para legibilidade: score = 1 - dist.
            resultados.append(
                ResultadoBusca(texto=texto, metadados=dict(meta), score=1.0 - float(dist))
            )
        return resultados

    # ------------------------------------------------------------------ #
    # Utilitários
    # ------------------------------------------------------------------ #
    def estatisticas(self) -> Dict[str, int]:
        """Retorna contagens úteis para diagnóstico/auditoria do índice."""
        ativos = self.collection.get(where={"arquivado": False}).get("ids") or []
        arquivados = self.collection.get(where={"arquivado": True}).get("ids") or []
        return {
            "chunks_ativos": len(ativos),
            "chunks_arquivados": len(arquivados),
            "total": len(ativos) + len(arquivados),
        }

    def resetar_colecao(self) -> None:
        """Apaga e recria a coleção (uso em reconstrução total/dev)."""
        import chromadb  # garante que o cliente já foi inicializado
        _ = self.collection
        self._client.delete_collection(self._config.collection_name)
        self._collection = None
        _ = self.collection  # recria vazia

    @staticmethod
    def _gerar_id_chunk(doc_id: str, indice: int) -> str:
        """ID determinístico e estável por (doc_id + versão embutida + índice).

        Como o doc_id já carrega a versão (ex.: ...-v3.2), o hash garante IDs
        únicos por versão; reindexar a mesma versão sobrescreve de forma idempotente.
        """
        bruto = f"{doc_id}::chunk::{indice}"
        digest = hashlib.sha1(bruto.encode("utf-8")).hexdigest()[:16]
        return f"{doc_id}--{indice:04d}--{digest}"

    @staticmethod
    def _sanitizar_metadados(metadados: Dict[str, object]) -> Dict[str, object]:
        """Garante que os metadados sejam de tipos aceitos pelo Chroma.

        O Chroma aceita apenas str, int, float e bool em metadados. Valores de
        outros tipos são serializados para string.
        """
        limpo: Dict[str, object] = {}
        for chave, valor in metadados.items():
            if isinstance(valor, (str, int, float, bool)):
                limpo[chave] = valor
            else:
                limpo[chave] = str(valor)
        return limpo
