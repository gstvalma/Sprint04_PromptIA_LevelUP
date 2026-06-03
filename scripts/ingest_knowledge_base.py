#!/usr/bin/env python3
"""
Script de ingestão da base de conhecimento clínica do BluaDiagnostics.

Lê os 5 documentos-âncora de `data/knowledge_base/`, executa o chunking
semântico-estrutural, gera embeddings com o modelo open-source configurado e
popula o índice vetorial Chroma em `data/vector_store/`.

Uso:
    # Ingestão incremental (versiona/arquiva versões antigas de cada doc_id):
    python scripts/ingest_knowledge_base.py

    # Reconstrução total do índice do zero:
    python scripts/ingest_knowledge_base.py --reset

    # Validação rápida: faz uma busca de teste ao final:
    python scripts/ingest_knowledge_base.py --teste-busca "alerta de AINE com losartana"

O script é IDEMPOTENTE: rodar duas vezes a mesma versão de um documento não
duplica chunks ativos; reindexar uma versão nova arquiva a anterior.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Permite executar o script diretamente (python scripts/...), ajustando o
# sys.path para enxergar o pacote 'src'.
RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from src.rag.config import DEFAULT_CONFIG, KNOWLEDGE_BASE_DIR
from src.rag.document_loader import carregar_knowledge_base
from src.rag.retriever import ClinicalRetriever


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Popula o vector store com a base de conhecimento clínica."
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Apaga a coleção e reconstrói o índice do zero.",
    )
    parser.add_argument(
        "--teste-busca",
        metavar="CONSULTA",
        default=None,
        help="Executa uma busca de validação ao final da ingestão.",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("  BluaDiagnostics — Ingestão da Base de Conhecimento Clínica (RAG)")
    print("=" * 70)
    print(f"  Modelo de embedding : {DEFAULT_CONFIG.embedding_model_name}")
    print(f"  Chunk (tokens)      : {DEFAULT_CONFIG.chunk_max_tokens} "
          f"(overlap {DEFAULT_CONFIG.chunk_overlap_tokens})")
    print(f"  Knowledge base      : {KNOWLEDGE_BASE_DIR}")
    print("-" * 70)

    # 1) Carrega os documentos da knowledge base.
    documentos = carregar_knowledge_base(KNOWLEDGE_BASE_DIR)
    print(f"  {len(documentos)} documento(s) encontrado(s).\n")

    # 2) Inicializa o retriever (carrega modelo e cliente Chroma sob demanda).
    retriever = ClinicalRetriever(config=DEFAULT_CONFIG)
    if args.reset:
        print("  [--reset] Reconstruindo o índice do zero...")
        try:
            retriever.resetar_colecao()
        except Exception:
            # Coleção pode ainda não existir; segue normalmente.
            pass

    # 3) Indexa cada documento, propagando os metadados de proveniência.
    total_chunks = 0
    for doc in documentos:
        doc_id = doc.metadados.get("doc_id", doc.caminho.name)
        n = retriever.indexar_documento(corpo=doc.corpo, metadados_documento=doc.metadados)
        total_chunks += n
        print(f"  ✓ {doc_id:38s} -> {n:3d} chunks")

    # 4) Relatório final.
    print("-" * 70)
    stats = retriever.estatisticas()
    print(f"  Total de chunks indexados nesta execução : {total_chunks}")
    print(f"  Chunks ativos no índice                  : {stats['chunks_ativos']}")
    print(f"  Chunks arquivados (versões antigas)      : {stats['chunks_arquivados']}")
    print("=" * 70)

    # 5) Busca de validação opcional.
    if args.teste_busca:
        print(f"\n  Busca de validação: «{args.teste_busca}»\n")
        resultados = retriever.buscar(args.teste_busca, top_k=3)
        for i, r in enumerate(resultados, start=1):
            origem = r.metadados.get("doc_id", "?")
            secao = r.metadados.get("secao", "?")
            print(f"  [{i}] score={r.score:.3f} | {origem} | {secao}")
            trecho = r.texto.replace("\n", " ")
            print(f"      {trecho[:160]}...\n")

    print("  Ingestão concluída com sucesso.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
