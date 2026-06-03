"""
Cérebro de raciocínio dos agentes + recuperador clínico de fallback.

Por que um "cérebro" injetável?
-------------------------------
Em produção, o raciocínio dos agentes (classificar intenção, sintetizar a
triagem, planejar ferramentas) é feito pelo Claude Sonnet 4. Mas amarrar o grafo
diretamente à API tornaria impossível rodá-lo/testá-lo sem chave e sem custo.

Aplicamos o mesmo princípio de inversão de dependência usado no RAG e nas tools:
os agentes recebem um objeto `Cerebro` (um Protocol). O padrão é o
`CerebroHeuristico` — determinístico, sem rede, sem API key — que faz o grafo
rodar e ser testado em qualquer máquina. Para produção, basta implementar o mesmo
Protocol chamando o Claude e injetá-lo no `construir_grafo`.

Também definimos aqui um `RecuperadorPorPalavraChave`: um fallback do RAG que
reusa o chunking dos 5 documentos da knowledge base e pontua por sobreposição de
palavras — para a Triagem funcionar mesmo sem o modelo de embedding (~2 GB) e sem
o índice Chroma populado.
"""

from __future__ import annotations

import unicodedata
from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable

from src.graph.state import (
    EstadoBlua,
    INTENCAO_AGENDAMENTO,
    INTENCAO_MEDICACAO,
    INTENCAO_TRIAGEM,
    INTENCAO_FORA_ESCOPO,
)
from src.tools.clinical_tools import TOKEN_MARIA


def _norm(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# =========================================================================== #
# Contrato do cérebro
# =========================================================================== #
@runtime_checkable
class Cerebro(Protocol):
    """Decisões de raciocínio que os agentes delegam ao cérebro."""

    def classificar_intencao(self, texto: str) -> str: ...
    def sintetizar_triagem(self, texto: str,
                           contexto: List[Dict[str, Any]]) -> Dict[str, Any]: ...
    def planejar_ferramentas(self, estado: EstadoBlua) -> List[Tuple[str, Dict[str, Any]]]: ...


# =========================================================================== #
# Implementação heurística determinística (padrão, offline)
# =========================================================================== #
class CerebroHeuristico:
    """Cérebro baseado em regras — não usa LLM. Determinístico e testável."""

    _MED = ["interaca", "medicament", "remedio", "posso tomar", "dose",
            "anti-inflamatorio", "antiinflamatorio", "ibuprofeno", "comprimido"]
    _AGENDA = ["agendar", "marcar consulta", "marcar uma consulta",
               "agende", "teleconsulta", "consulta com"]
    _SINTOMAS = ["dor", "febre", "tosse", "tontura", "pressao", "sintoma",
                 "falta de ar", "cabeca", "peito", "ansiedade", "triagem"]
    _FORA = ["previsao do tempo", "restaurante", "receita de bolo",
             "resultado do jogo", "piada", "poema"]

    def classificar_intencao(self, texto: str) -> str:
        t = _norm(texto)
        if any(p in t for p in self._FORA) and not any(s in t for s in self._SINTOMAS):
            return INTENCAO_FORA_ESCOPO
        if any(p in t for p in self._AGENDA):
            return INTENCAO_AGENDAMENTO
        if any(p in t for p in self._MED):
            return INTENCAO_MEDICACAO
        # Padrão: qualquer queixa clínica é tratada como triagem.
        return INTENCAO_TRIAGEM

    def sintetizar_triagem(self, texto: str,
                           contexto: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Produz uma classificação de triagem simplificada e auditável.

        Em produção, o Claude geraria o JSON estruturado do System Prompt a partir
        do contexto RAG. Aqui, derivamos uma classificação determinística baseada
        em palavras-chave + o melhor chunk recuperado.
        """
        t = _norm(texto)
        # Heurística de cor Manchester (apenas casos não-críticos chegam aqui;
        # os críticos são interceptados pelos guardrails antes da triagem).
        if any(p in t for p in ["forte", "intensa", "piora", "ha 2 dias", "vomito"]):
            cor, especialidade = "AMARELO", "clinica_medica"
        elif any(p in t for p in ["ansiedade", "depress", "panico", "triste"]):
            cor, especialidade = "VERDE", "psiquiatria"
        else:
            cor, especialidade = "VERDE", "clinica_medica"

        melhor = contexto[0]["doc_id"] if contexto else "sem_contexto"
        return {
            "classificacao_manchester": cor,
            "especialidade_sugerida": especialidade,
            "nivel_confianca": "ALTO",   # casos claros; <70% acionaria escalada ALTO
            "raciocinio": ("Classificação preliminar baseada na queixa e na "
                           f"evidência recuperada ({melhor}). Requer validação do "
                           "operador. NÃO constitui diagnóstico (R1)."),
            "fontes_rag": [c.get("doc_id") for c in contexto],
        }

    def planejar_ferramentas(self, estado: EstadoBlua
                             ) -> List[Tuple[str, Dict[str, Any]]]:
        """Decide quais ferramentas chamar e com quais argumentos (determinístico)."""
        paciente = estado.get("paciente_atual") or {}
        token = paciente.get("token", TOKEN_MARIA)
        sessao = estado.get("sessao_id", "SESS-DEMO000001")
        intencao = estado.get("intencao", INTENCAO_TRIAGEM)
        texto = _norm(estado.get("entrada_operador", ""))

        plano: List[Tuple[str, Dict[str, Any]]] = []

        # 1) Sempre consultar histórico (leitura) para contextualizar.
        plano.append(("consultar_historico_paciente", {
            "patient_token": token,
            "secoes_solicitadas": ["medicamentos_em_uso", "alergias",
                                   "comorbidades_ativas"],
            "sessao_triagem_id": sessao,
            "profundidade": "resumo",
        }))

        # 2) Se há menção a novo medicamento -> verificar interações.
        novo_med = None
        if "ibuprofeno" in texto or "anti-inflamatorio" in texto or "antiinflamatorio" in texto:
            novo_med = "ibuprofeno"
        if intencao == INTENCAO_MEDICACAO and novo_med:
            # A verificação real usa os medicamentos do histórico (resolvidos no agente).
            plano.append(("__verificar_interacoes_com_historico__", {
                "novo_medicamento": novo_med,
                "sessao_triagem_id": sessao,
                "perfil_paciente": {
                    "idade_anos": paciente.get("idade_anos", 34),
                    "sexo_biologico": paciente.get("sexo_biologico", "feminino"),
                },
            }))

        # 3) Se a intenção é agendamento -> propor teleconsulta (sem confirmar).
        if intencao == INTENCAO_AGENDAMENTO:
            especialidade = (estado.get("classificacao_triagem", {})
                             .get("especialidade_sugerida", "clinica_medica"))
            plano.append(("agendar_teleconsulta", {
                "patient_token": token,
                "especialidade": especialidade,
                "prioridade": "rotina",
                "motivo_consulta": estado.get("entrada_operador", "")[:480],
                "operador_id": estado.get("operador_id", "OP-DEMO0001"),
                "sessao_triagem_id": sessao,
                "modalidade": "video",
                "confirmacao_operador": False,   # human-in-the-loop
            }))

        return plano


# =========================================================================== #
# Recuperador clínico de fallback (sem embeddings / sem Chroma)
# =========================================================================== #
class _ResultadoSimples:
    """Mesma forma de ResultadoBusca (texto, metadados, score), sem depender do RAG."""
    def __init__(self, texto: str, metadados: Dict[str, Any], score: float):
        self.texto = texto
        self.metadados = metadados
        self.score = score


class RecuperadorPorPalavraChave:
    """Fallback do RAG: chunka os 5 .md e pontua por sobreposição de palavras.

    Reusa o `SemanticChunker` e o `document_loader` já existentes, garantindo
    coerência com o pipeline real. Roda offline, sem modelo de embedding.
    """

    def __init__(self) -> None:
        from src.rag.config import KNOWLEDGE_BASE_DIR
        from src.rag.chunking import SemanticChunker
        from src.rag.embeddings import contador_de_tokens_heuristico
        from src.rag.document_loader import carregar_knowledge_base

        chunker = SemanticChunker(contar_tokens=contador_de_tokens_heuristico)
        self._chunks: List[Dict[str, Any]] = []
        try:
            for doc in carregar_knowledge_base(KNOWLEDGE_BASE_DIR):
                for c in chunker.dividir(doc.corpo, doc.metadados):
                    self._chunks.append({"texto": c.texto, "meta": c.metadados,
                                         "tokens": set(_norm(c.texto).split())})
        except FileNotFoundError:
            # Sem knowledge base disponível: retriever vazio (Triagem segue sem RAG).
            self._chunks = []

    def buscar(self, consulta: str, top_k: int = 3, **_: Any) -> List[_ResultadoSimples]:
        termos = set(_norm(consulta).split())
        ranqueados = []
        for ch in self._chunks:
            overlap = len(termos & ch["tokens"])
            if overlap:
                ranqueados.append((overlap, ch))
        ranqueados.sort(key=lambda x: x[0], reverse=True)
        saida = []
        for score, ch in ranqueados[:top_k]:
            saida.append(_ResultadoSimples(
                texto=ch["texto"], metadados=ch["meta"], score=float(score)))
        return saida
