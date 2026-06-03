"""
Carregador de documentos da base de conhecimento clínica.

Cada documento-âncora é um arquivo markdown com um bloco de FRONT-MATTER YAML no
topo (delimitado por '---'), contendo os metadados de proveniência exigidos pela
Sprint 1: doc_id, fonte, versão, última atualização, grau de evidência e
responsável técnico.

Exemplo de cabeçalho esperado:

    ---
    doc_id: DOC-TRIAGE-MANCHESTER-CP-v3.2
    titulo: Protocolo de Triagem Manchester Adaptado Care Plus
    fonte: Manchester Triage Group + adaptações internas Care Plus
    versao: "3.2"
    ...
    ---

    # Corpo do documento em markdown...

O parser de front-matter é propositalmente minimalista (sem dependência de
PyYAML) para reduzir a superfície de dependências do pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List


@dataclass
class DocumentoClinico:
    """Documento clínico carregado da knowledge base."""

    caminho: Path
    metadados: Dict[str, str] = field(default_factory=dict)
    corpo: str = ""


def _parse_front_matter(conteudo: str) -> tuple[Dict[str, str], str]:
    """Separa o front-matter YAML do corpo markdown.

    Returns:
        (metadados, corpo). Se não houver front-matter, retorna ({}, conteúdo).
    """
    linhas = conteudo.splitlines()
    if not linhas or linhas[0].strip() != "---":
        return {}, conteudo

    # Procura o delimitador de fechamento do front-matter.
    fim = None
    for i in range(1, len(linhas)):
        if linhas[i].strip() == "---":
            fim = i
            break
    if fim is None:
        return {}, conteudo

    metadados: Dict[str, str] = {}
    for linha in linhas[1:fim]:
        if not linha.strip() or ":" not in linha:
            continue
        chave, _, valor = linha.partition(":")
        valor = valor.strip().strip('"').strip("'")
        metadados[chave.strip()] = valor

    corpo = "\n".join(linhas[fim + 1:]).strip()
    return metadados, corpo


def carregar_documento(caminho: Path) -> DocumentoClinico:
    """Carrega e parseia um único arquivo markdown da knowledge base."""
    conteudo = caminho.read_text(encoding="utf-8")
    metadados, corpo = _parse_front_matter(conteudo)
    return DocumentoClinico(caminho=caminho, metadados=metadados, corpo=corpo)


def carregar_knowledge_base(diretorio: Path) -> List[DocumentoClinico]:
    """Carrega todos os documentos .md de um diretório, em ordem estável."""
    if not diretorio.exists():
        raise FileNotFoundError(
            f"Diretório da knowledge base não encontrado: {diretorio}"
        )
    documentos = [
        carregar_documento(p) for p in sorted(diretorio.glob("*.md"))
    ]
    if not documentos:
        raise FileNotFoundError(
            f"Nenhum documento .md encontrado em {diretorio}."
        )
    return documentos
