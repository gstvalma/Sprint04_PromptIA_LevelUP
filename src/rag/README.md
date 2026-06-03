# Pipeline de RAG — BluaDiagnostics (Sprint 2)

Pipeline de Recuperação Aumentada por Geração (RAG) que indexa a base de
conhecimento clínica da Care Plus e recupera evidência relevante para apoiar a
triagem do assistente digital.

> **Segurança clínica:** o RAG apenas **recupera** evidência. Toda resposta
> gerada a partir dela passa, a jusante, pelos guardrails e pelo
> *human-in-the-loop* definidos na Sprint 1. O sistema não substitui o
> julgamento médico.

## Componentes

| Módulo | Responsabilidade |
|---|---|
| `config.py` | Fonte única de verdade (modelo, chunking, paths, top-k). |
| `embeddings.py` | Embeddings open-source (`intfloat/multilingual-e5-large`) com prefixos E5 e contagem de tokens. |
| `chunking.py` | Chunking robusto: estrutural (cabeçalhos markdown) + orçamento de 512 tokens + overlap de 50. |
| `document_loader.py` | Leitura dos `.md` da KB e parsing do front-matter YAML. |
| `retriever.py` | Orquestra embeddings + **ChromaDB**; indexação versionada e busca Top-K filtrável. |

## Decisões de arquitetura

- **ChromaDB** (e não FAISS): filtragem nativa por metadados (`doc_id`,
  `versao`, `grau_evidencia`), persistência local embutida e re-indexação
  versionada — chunks de versões antigas são **arquivados**, não deletados
  (auditabilidade). Em produção, o alvo é o Weaviate (Sprint 1).
- **Embedding multilíngue E5**: corpus 100% PT-BR clínico; convenção de
  prefixos `query:`/`passage:` implementada corretamente.
- **Backend de embedding injetável**: permite *stub* determinístico em testes
  sem baixar modelos de GBs (princípio de inversão de dependência).

## Como executar

```bash
# 1. Instalar dependências
pip install -r requirements.txt

# 2. Popular o vector store com os 5 documentos-âncora da KB
python scripts/ingest_knowledge_base.py

# Reconstrução total do índice
python scripts/ingest_knowledge_base.py --reset

# Ingestão + busca de validação
python scripts/ingest_knowledge_base.py --teste-busca "alerta de AINE com losartana"
```

Uso programático:

```python
from src.rag import ClinicalRetriever

retriever = ClinicalRetriever()
for r in retriever.buscar("ideação suicida em teleconsulta", top_k=3):
    print(r.score, r.metadados["doc_id"], r.metadados["secao"])
```

## Base de conhecimento (`data/knowledge_base/`)

| Arquivo | doc_id | Domínio | Evidência |
|---|---|---|---|
| `01_triagem_manchester.md` | `DOC-TRIAGE-MANCHESTER-CP-v3.2` | Triagem | A |
| `02_politica_telemedicina.md` | `DOC-POLICY-TELEMED-CP-v2.1` | Política/Compliance | Normativo |
| `03_interacoes_medicamentosas.md` | `DOC-RX-INTERACTIONS-CP-v1.4` | Farmacologia | A/B |
| `04_doencas_cronicas.md` | `DOC-CHRONIC-DISEASE-CP-v2.0` | Crônicos | A |
| `05_saude_mental.md` | `DOC-MENTAL-HEALTH-CP-v1.0` | Saúde Mental | B |
