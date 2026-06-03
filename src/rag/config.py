"""
Configuração central do pipeline de RAG do BluaDiagnostics.

Este módulo concentra **toda** a parametrização do RAG em um único lugar
(fonte única de verdade). Qualquer mudança de modelo de embedding, tamanho de
chunk ou caminho de persistência deve ser feita aqui — nunca espalhada pelo
código. Isso facilita auditoria, reprodutibilidade e versionamento, requisitos
explícitos do projeto Care Plus.

Os valores padrão de chunking (512 tokens / overlap de 50) seguem fielmente a
decisão registrada na Especificação Técnica v1.1 da Sprint 1.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# Caminhos do projeto
# --------------------------------------------------------------------------- #
# Raiz do repositório, derivada a partir da localização deste arquivo:
#   src/rag/config.py  ->  sobe 3 níveis  ->  raiz do projeto
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

# Diretório onde ficam os 5 documentos-âncora clínicos (.md com front-matter).
KNOWLEDGE_BASE_DIR: Path = PROJECT_ROOT / "data" / "knowledge_base"

# Diretório de persistência do índice vetorial (Chroma). É um ARTEFATO DERIVADO:
# pode ser reconstruído a qualquer momento a partir da knowledge_base, portanto
# fica fora do controle de versão (ver .gitignore).
VECTOR_STORE_DIR: Path = PROJECT_ROOT / "data" / "vector_store"


@dataclass(frozen=True)
class RagConfig:
    """Parâmetros imutáveis do pipeline de RAG.

    Atributos:
        embedding_model_name:
            Identificador do modelo de embedding no Hugging Face. O padrão é o
            'intfloat/multilingual-e5-large', escolhido por seu desempenho
            superior em português e por ser open-source/self-hosted (requisito
            de soberania de dados e LGPD da Care Plus).
        embedding_uses_e5_prefix:
            Se True, aplica a convenção de prefixos da família E5
            ('query: ' para consultas e 'passage: ' para documentos). Modelos
            E5 são treinados com esses prefixos; omiti-los degrada a recuperação.
            Para modelos que NÃO seguem essa convenção (ex.: MiniLM puro), defina
            como False.
        chunk_max_tokens:
            Tamanho-alvo máximo de cada chunk, medido em tokens do tokenizer do
            modelo de embedding. Padrão 512 (decisão da Sprint 1).
        chunk_overlap_tokens:
            Sobreposição entre chunks consecutivos, em tokens. Padrão 50. A
            sobreposição preserva contexto que cruza fronteiras de chunk
            (ex.: uma conduta clínica que começa no fim de um chunk).
        collection_name:
            Nome da coleção no Chroma onde os chunks clínicos são indexados.
        distance_metric:
            Métrica de distância da coleção Chroma. 'cosine' é a recomendada
            para embeddings normalizados da família E5.
        retrieval_top_k:
            Número de chunks recuperados por padrão em uma consulta. Padrão 3,
            alinhado ao 'Top-3' definido no fluxo arquitetural da Sprint 1.
        device:
            Dispositivo de inferência do modelo de embedding ('cpu' ou 'cuda').
            Lido da variável de ambiente BLUA_EMBED_DEVICE quando presente.
    """

    embedding_model_name: str = "intfloat/multilingual-e5-large"
    embedding_uses_e5_prefix: bool = True

    chunk_max_tokens: int = 512
    chunk_overlap_tokens: int = 50

    collection_name: str = "blua_kb_clinica"
    distance_metric: str = "cosine"

    retrieval_top_k: int = 3

    device: str = field(
        default_factory=lambda: os.environ.get("BLUA_EMBED_DEVICE", "cpu")
    )

    def __post_init__(self) -> None:
        # Validações defensivas: falham cedo e com mensagem clara.
        if self.chunk_overlap_tokens >= self.chunk_max_tokens:
            raise ValueError(
                "chunk_overlap_tokens deve ser menor que chunk_max_tokens "
                f"(recebido overlap={self.chunk_overlap_tokens}, "
                f"max={self.chunk_max_tokens})."
            )
        if self.retrieval_top_k < 1:
            raise ValueError("retrieval_top_k deve ser >= 1.")


# Instância padrão pronta para uso em todo o projeto.
DEFAULT_CONFIG = RagConfig()
