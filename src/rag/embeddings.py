"""
Camada de embeddings open-source do BluaDiagnostics.

Encapsula o modelo de embedding (por padrão 'intfloat/multilingual-e5-large')
atrás de uma interface mínima e estável, de modo que o restante do pipeline
(chunker e retriever) NÃO dependa diretamente de detalhes do
sentence-transformers. Isso segue o princípio de inversão de dependência:
trocar o modelo de embedding no futuro não exige tocar no retriever.

Responsabilidades desta camada:
  1. Carregar o modelo uma única vez (carga preguiçosa / lazy).
  2. Aplicar a convenção de prefixos da família E5 ('query:' / 'passage:').
  3. Normalizar os vetores (essencial para distância de cosseno).
  4. Expor um contador de tokens baseado no MESMO tokenizer do modelo, para que
     o chunking seja realmente "token-aware" e coerente com o que o modelo vê.
"""

from __future__ import annotations

from typing import List, Protocol, Sequence, runtime_checkable

from .config import DEFAULT_CONFIG, RagConfig


@runtime_checkable
class EmbeddingBackend(Protocol):
    """Contrato mínimo que qualquer backend de embedding deve cumprir.

    Definir esse Protocol permite injetar implementações alternativas (por
    exemplo, um stub determinístico em testes, ou um endpoint gerenciado em
    produção) sem alterar o retriever.
    """

    def embed_documents(self, textos: Sequence[str]) -> List[List[float]]:
        """Gera embeddings para uma lista de PASSAGENS (documentos/chunks)."""
        ...

    def embed_query(self, texto: str) -> List[float]:
        """Gera o embedding de uma CONSULTA (pergunta do usuário/operador)."""
        ...

    def count_tokens(self, texto: str) -> int:
        """Conta tokens de um texto usando o tokenizer do modelo."""
        ...


class SentenceTransformerEmbedder:
    """Backend de embedding baseado em `sentence-transformers`.

    Projetado para a família E5 multilíngue, mas funciona com qualquer modelo
    do sentence-transformers desde que `usa_prefixo_e5` seja ajustado em
    conformidade na configuração.
    """

    def __init__(self, config: RagConfig = DEFAULT_CONFIG) -> None:
        self._config = config
        self._model = None  # carga preguiçosa: só instancia ao primeiro uso

    # ------------------------------------------------------------------ #
    # Carga preguiçosa do modelo
    # ------------------------------------------------------------------ #
    @property
    def model(self):
        """Carrega o SentenceTransformer sob demanda (lazy loading).

        O import é feito aqui dentro (e não no topo do módulo) de propósito:
        assim, módulos que apenas precisam do CONTRATO (Protocol) ou do contador
        de tokens via outro backend não pagam o custo de importar torch.
        """
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:  # pragma: no cover - guarda de dependência
                raise ImportError(
                    "A biblioteca 'sentence-transformers' não está instalada. "
                    "Instale as dependências do RAG com:\n"
                    "    pip install -r requirements.txt"
                ) from exc

            self._model = SentenceTransformer(
                self._config.embedding_model_name,
                device=self._config.device,
            )
        return self._model

    # ------------------------------------------------------------------ #
    # Convenção de prefixos da família E5
    # ------------------------------------------------------------------ #
    def _prefixar(self, texto: str, tipo: str) -> str:
        """Adiciona o prefixo E5 adequado ('query: ' ou 'passage: ').

        Modelos E5 foram treinados com esses prefixos; aplicá-los corretamente
        melhora de forma mensurável a qualidade da recuperação. Quando o modelo
        não segue essa convenção (config.embedding_uses_e5_prefix=False), o
        texto é retornado sem alteração.
        """
        if not self._config.embedding_uses_e5_prefix:
            return texto
        if tipo == "query":
            return f"query: {texto}"
        return f"passage: {texto}"

    # ------------------------------------------------------------------ #
    # API pública
    # ------------------------------------------------------------------ #
    def embed_documents(self, textos: Sequence[str]) -> List[List[float]]:
        """Gera embeddings normalizados para uma lista de passagens (chunks)."""
        passagens = [self._prefixar(t, "passage") for t in textos]
        vetores = self.model.encode(
            passagens,
            normalize_embeddings=True,  # vetores unitários -> cosseno estável
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return vetores.tolist()

    def embed_query(self, texto: str) -> List[float]:
        """Gera o embedding normalizado de uma única consulta."""
        consulta = self._prefixar(texto, "query")
        vetor = self.model.encode(
            consulta,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return vetor.tolist()

    def count_tokens(self, texto: str) -> int:
        """Conta tokens com o tokenizer nativo do modelo de embedding.

        Usar o tokenizer do próprio modelo torna o chunking coerente com o
        limite real de contexto do encoder, evitando estourar a janela do
        modelo (E5 large aceita até 512 tokens por passagem).
        """
        # add_special_tokens=False para contar apenas o conteúdo, sem [CLS]/[SEP].
        ids = self.model.tokenizer.encode(texto, add_special_tokens=False)
        return len(ids)


def contador_de_tokens_heuristico(texto: str) -> int:
    """Estimador de tokens SEM dependência de modelo (fallback offline).

    Aproxima a contagem de tokens a partir do número de palavras, usando o fator
    empírico ~1.3 token/palavra observado para português em tokenizadores
    subword. É usado apenas quando o tokenizer real não está disponível (por
    exemplo, em testes de unidade do chunker que não devem baixar o modelo).
    Não substitui a contagem real em produção.
    """
    n_palavras = len(texto.split())
    return max(1, int(round(n_palavras * 1.3)))
