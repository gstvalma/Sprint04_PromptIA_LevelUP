"""
Estratégia de chunking semântico-estrutural do BluaDiagnostics.

Por que NÃO usar chunking ingênuo por caracteres
-------------------------------------------------
Dividir o texto a cada N caracteres quebra frases no meio, separa uma conduta
clínica do medicamento a que ela se refere e destrói a estrutura do documento.
Em um domínio clínico, isso produz chunks que recuperam mal e podem induzir o
LLM a respostas perigosamente incompletas.

Estratégia adotada (em três camadas)
-------------------------------------
1. CAMADA ESTRUTURAL: o documento é primeiro quebrado pela sua hierarquia de
   cabeçalhos markdown (##, ###, ####). Cada seção vira uma unidade coesa e
   carrega seu "caminho de seção" (ex.: "Interações > Anticoagulantes") como
   metadado, o que melhora a interpretabilidade dos resultados de busca.

2. CAMADA DE ORÇAMENTO DE TOKENS: seções grandes são subdivididas por
   PARÁGRAFOS/sentenças, acumulando texto até atingir o orçamento de
   `chunk_max_tokens` (padrão 512). Nunca quebramos no meio de uma frase quando
   é possível evitar.

3. CAMADA DE OVERLAP: chunks consecutivos compartilham `chunk_overlap_tokens`
   (padrão 50) de cauda/cabeça, preservando o contexto que cruza fronteiras.

O contador de tokens é INJETADO (parâmetro `contar_tokens`), o que permite usar
o tokenizer real do modelo em produção e um estimador heurístico em testes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List

from .config import DEFAULT_CONFIG, RagConfig


@dataclass
class Chunk:
    """Unidade indexável: um trecho de texto + metadados de proveniência."""

    texto: str
    metadados: Dict[str, object] = field(default_factory=dict)


# Regex para detectar cabeçalhos markdown ATX (## Título, ### Subtítulo, ...).
_PADRAO_CABECALHO = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass
class _Secao:
    """Seção estrutural intermediária extraída do markdown."""

    caminho: str  # ex.: "Documento > Interações > Anticoagulantes"
    nivel: int
    texto: str


class SemanticChunker:
    """Divide documentos markdown clínicos em chunks coesos e auditáveis."""

    def __init__(
        self,
        config: RagConfig = DEFAULT_CONFIG,
        contar_tokens: Callable[[str], int] | None = None,
    ) -> None:
        """
        Args:
            config: parâmetros de chunking (tamanho-alvo e overlap).
            contar_tokens: função que conta tokens de um texto. Em produção,
                passe `SentenceTransformerEmbedder(...).count_tokens`. Se omitido,
                usa o estimador heurístico (apropriado para testes offline).
        """
        self._config = config
        if contar_tokens is None:
            from .embeddings import contador_de_tokens_heuristico

            contar_tokens = contador_de_tokens_heuristico
        self._contar_tokens = contar_tokens

    # ------------------------------------------------------------------ #
    # API pública
    # ------------------------------------------------------------------ #
    def dividir(self, texto: str, metadados_base: Dict[str, object]) -> List[Chunk]:
        """Transforma um documento inteiro em uma lista de chunks.

        Args:
            texto: corpo do documento em markdown (sem o front-matter YAML).
            metadados_base: metadados do documento (doc_id, fonte, versão, grau
                de evidência, etc.) que serão propagados para todos os chunks.

        Returns:
            Lista de Chunk, cada um com texto e metadados enriquecidos
            (caminho da seção e índice do chunk).
        """
        secoes = self._dividir_em_secoes(texto)

        chunks: List[Chunk] = []
        indice_global = 0
        for secao in secoes:
            for trecho in self._dividir_secao_por_tokens(secao.texto):
                meta = dict(metadados_base)
                meta["secao"] = secao.caminho
                meta["chunk_index"] = indice_global
                meta["n_tokens"] = self._contar_tokens(trecho)
                chunks.append(Chunk(texto=trecho, metadados=meta))
                indice_global += 1
        return chunks

    # ------------------------------------------------------------------ #
    # Camada 1 — divisão estrutural por cabeçalhos markdown
    # ------------------------------------------------------------------ #
    def _dividir_em_secoes(self, texto: str) -> List[_Secao]:
        """Quebra o markdown em seções respeitando a hierarquia de cabeçalhos."""
        linhas = texto.splitlines()
        secoes: List[_Secao] = []

        # Pilha de títulos ativos por nível, para reconstruir o "caminho".
        pilha: List[tuple[int, str]] = []
        buffer: List[str] = []
        caminho_atual = ""
        nivel_atual = 0

        def _descarregar() -> None:
            """Materializa o buffer acumulado como uma seção, se não estiver vazio."""
            nonlocal buffer
            corpo = "\n".join(buffer).strip()
            if corpo:
                secoes.append(
                    _Secao(caminho=caminho_atual or "(raiz)",
                           nivel=nivel_atual,
                           texto=corpo)
                )
            buffer = []

        for linha in linhas:
            m = _PADRAO_CABECALHO.match(linha)
            if m:
                # Ao encontrar um novo cabeçalho, fecha a seção anterior.
                _descarregar()
                nivel = len(m.group(1))
                titulo = m.group(2).strip()

                # Atualiza a pilha de títulos para montar o caminho hierárquico.
                while pilha and pilha[-1][0] >= nivel:
                    pilha.pop()
                pilha.append((nivel, titulo))
                caminho_atual = " > ".join(t for _, t in pilha)
                nivel_atual = nivel
            else:
                buffer.append(linha)

        _descarregar()

        # Documento sem nenhum cabeçalho: trata o corpo inteiro como uma seção.
        if not secoes and texto.strip():
            secoes.append(_Secao(caminho="(raiz)", nivel=0, texto=texto.strip()))
        return secoes

    # ------------------------------------------------------------------ #
    # Camadas 2 e 3 — orçamento de tokens + overlap
    # ------------------------------------------------------------------ #
    def _dividir_secao_por_tokens(self, texto: str) -> List[str]:
        """Subdivide uma seção em chunks respeitando o orçamento de tokens.

        Acumula parágrafos/sentenças até o limite de tokens e aplica overlap
        entre chunks consecutivos.
        """
        unidades = self._segmentar_em_unidades(texto)
        if not unidades:
            return []

        chunks: List[str] = []
        atual: List[str] = []
        tokens_atual = 0

        for unidade in unidades:
            t_unidade = self._contar_tokens(unidade)

            # Unidade isolada maior que o orçamento: quebra forçada por tokens.
            if t_unidade > self._config.chunk_max_tokens:
                if atual:
                    chunks.append(" ".join(atual).strip())
                    atual, tokens_atual = [], 0
                chunks.extend(self._quebra_forcada(unidade))
                continue

            # Caso normal: fecharia o chunk se ultrapassasse o orçamento.
            if tokens_atual + t_unidade > self._config.chunk_max_tokens and atual:
                chunks.append(" ".join(atual).strip())
                atual, tokens_atual = self._iniciar_com_overlap(atual)

            atual.append(unidade)
            tokens_atual += t_unidade

        if atual:
            chunks.append(" ".join(atual).strip())

        return [c for c in chunks if c]

    def _segmentar_em_unidades(self, texto: str) -> List[str]:
        """Divide o texto em parágrafos e, dentro deles, em sentenças.

        Parágrafos (separados por linha em branco) são a unidade primária;
        parágrafos longos são refinados em sentenças por pontuação final.
        """
        paragrafos = [p.strip() for p in re.split(r"\n\s*\n", texto) if p.strip()]
        unidades: List[str] = []
        for p in paragrafos:
            if self._contar_tokens(p) <= self._config.chunk_max_tokens:
                unidades.append(p)
            else:
                # Divisão por sentença, preservando o delimitador (.!?;:).
                sentencas = re.split(r"(?<=[.!?;:])\s+", p)
                unidades.extend(s.strip() for s in sentencas if s.strip())
        return unidades

    def _iniciar_com_overlap(self, anterior: List[str]) -> tuple[List[str], int]:
        """Monta o início do próximo chunk reaproveitando a cauda do anterior.

        Pega unidades do fim do chunk anterior até acumular ~overlap tokens,
        garantindo continuidade de contexto entre chunks.
        """
        overlap_unidades: List[str] = []
        tokens = 0
        for unidade in reversed(anterior):
            t = self._contar_tokens(unidade)
            if tokens + t > self._config.chunk_overlap_tokens and overlap_unidades:
                break
            overlap_unidades.insert(0, unidade)
            tokens += t
        return overlap_unidades, tokens

    def _quebra_forcada(self, texto: str) -> List[str]:
        """Último recurso: quebra um trecho muito longo por janela de palavras.

        Acionado apenas quando uma única sentença excede o orçamento de tokens
        (raro em texto clínico bem-formatado). Mantém overlap aproximado.
        """
        palavras = texto.split()
        chunks: List[str] = []
        inicio = 0
        # Estima palavras por chunk a partir do orçamento de tokens.
        passo = max(1, int(self._config.chunk_max_tokens / 1.3))
        overlap_palavras = max(0, int(self._config.chunk_overlap_tokens / 1.3))
        while inicio < len(palavras):
            fim = inicio + passo
            chunks.append(" ".join(palavras[inicio:fim]).strip())
            inicio = fim - overlap_palavras
            if inicio <= 0:
                inicio = fim
        return chunks
