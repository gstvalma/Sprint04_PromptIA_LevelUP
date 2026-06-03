# BluaDiagnostics — Documento de Arquitetura de IA
**Care Plus | Assistente Digital de Check-up Proativo e Prescrição Remota**
*Versão 1.0 — Elaborado por: AI Architect | Maio de 2026*

---

## Sumário Executivo

O **BluaDiagnostics** é uma plataforma de saúde digital orientada por Inteligência Artificial, projetada para a operadora de saúde premium **Care Plus**. Sua missão é oferecer check-ups proativos, triagem clínica assistida e suporte à prescrição remota, ampliando o acesso qualificado à saúde sem substituir a autoridade médica. Este documento define a persona-alvo do sistema, mapeia os riscos clínicos e éticos envolvidos, propõe mitigações arquiteturais e compara os principais modelos de linguagem disponíveis para embasar a decisão de infraestrutura.

---

## 1. Persona-Alvo: Definição e Justificativa

### 1.1 Persona Selecionada: Operador de Triagem (Triage Operator)

Após análise das três opções disponíveis — paciente usuário final, médico pós-teleconsulta e operador de triagem —, o **Operador de Triagem** foi selecionado como persona primária do BluaDiagnostics em sua fase inicial de implantação.

### 1.2 Perfil da Persona

| Atributo | Descrição |
|---|---|
| **Função** | Profissional de saúde de nível técnico ou enfermeiro(a) que realiza a triagem inicial dos beneficiários Care Plus |
| **Canal de acesso** | Interface web corporativa integrada ao prontuário eletrônico (PEP) |
| **Nível de literacia digital** | Intermediário a avançado |
| **Contexto de uso** | Central de saúde remota, UBS conveniadas, ou atendimento domiciliar assistido |
| **Necessidade central** | Agilizar a classificação de risco do paciente, sugerir encaminhamentos e preparar o prontuário para a consulta médica |
| **Dor principal** | Volume elevado de atendimentos, falta de tempo para anamnese detalhada, inconsistência na coleta de dados clínicos |

### 1.3 Justificativa da Escolha

**a) Segurança clínica e regulatória**
O operador de triagem é um profissional treinado, capaz de validar, questionar e corrigir saídas do modelo antes que qualquer informação chegue ao paciente ou ao médico. Isso garante que o BluaDiagnostics opere sob supervisão humana qualificada — condição essencial diante das exigências do CFM (Conselho Federal de Medicina), da Resolução CFM nº 2.314/2022 e das diretrizes da ANPD para sistemas de decisão automatizada em saúde.

**b) Máximo impacto operacional no curto prazo**
O gargalo mais crítico na jornada do beneficiário Care Plus é a triagem inicial. Um operador assistido por IA pode atender entre 3x e 5x mais pacientes por turno com maior padronização clínica, reduzindo filas de espera e melhorando a experiência do beneficiário.

**c) Menor risco de alucinação com consequências diretas ao paciente**
Diferentemente do paciente final, o operador possui conhecimento técnico para identificar respostas clinicamente absurdas do modelo. Isso cria uma camada de validação humana (human-in-the-loop) estrutural, não apenas procedimental.

**d) Melhor dataset de retroalimentação**
As correções feitas pelo operador sobre as sugestões do modelo geram dados de alta qualidade para fine-tuning contínuo, acelerando a maturidade clínica do BluaDiagnostics.

**e) Diferencial competitivo da Care Plus**
A Care Plus posiciona-se como operadora premium. Dotar seus operadores de triagem com IA avançada eleva o padrão percebido de atendimento sem expor a marca ao risco de diagnósticos incorretos entregues diretamente ao beneficiário sem mediação profissional.

> **Decisão arquitetural resultante:** O BluaDiagnostics opera no modelo **AI-Assisted Professional** (profissional assistido por IA), não AI-to-Patient direto. A interface apresenta sugestões clínicas ao operador, que as confirma, ajusta ou rejeita antes de qualquer ação downstream.

---

## 2. Mapeamento de Riscos Clínicos e Éticos

### 2.1 Visão Geral do Mapa de Risco

```
┌──────────────────────────────────────────────────────────────────┐
│                    MATRIZ DE RISCO — BluaDiagnostics             │
├────────────────────┬──────────────────┬──────────────────────────┤
│ Risco              │ Probabilidade    │ Severidade               │
├────────────────────┼──────────────────┼──────────────────────────┤
│ Alucinação clínica │ Alta             │ Crítica                  │
│ Viés algorítmico   │ Média            │ Alta                     │
│ Violação LGPD      │ Baixa-Média      │ Crítica                  │
│ Falha human-loop   │ Média            │ Alta                     │
│ Prompt injection   │ Baixa            │ Alta                     │
│ Deriva de modelo   │ Média            │ Média                    │
└────────────────────┴──────────────────┴──────────────────────────┘
```

---

### 2.2 Risco 1: Alucinações Clínicas

#### Descrição do Risco
Modelos de linguagem podem gerar informações médicas plausíveis, porém factualmente incorretas — incluindo diagnósticos errôneos, dosagens inadequadas, interações medicamentosas inexistentes ou indicações terapêuticas obsoletas.

**Exemplo concreto de alucinação perigosa:**
O modelo sugere "metformina 850mg 2x/dia para paciente com TFG de 28 mL/min/1,73m²" — dose contraindicada em insuficiência renal moderada/grave.

#### Mitigações Arquiteturais

| Camada | Mitigação | Implementação |
|---|---|---|
| **Retrieval-Augmented Generation (RAG)** | Ancoragem em bases clínicas validadas | Integração com ANVISA, CID-11, DRUGDEX, Micromedex e protocolos internos Care Plus via vector database (ex.: Weaviate ou Pinecone) |
| **Guardrails de saída** | Validação pós-geração | Pipeline de NLP estruturado com checagem de entidades médicas contra ontologias (SNOMED CT, RxNorm) antes da exibição ao operador |
| **Confidence scoring** | Indicação de incerteza** | Toda sugestão exibe score de confiança; abaixo de 75%, o sistema exibe alerta vermelho e bloqueia o encaminhamento automático |
| **Citation enforcement** | Rastreabilidade** | O modelo é instruído via system prompt a citar obrigatoriamente a fonte clínica de cada recomendação |
| **Feedback loop estruturado** | Aprendizado contínuo | Operador avalia cada sugestão (aceita/rejeita/corrige); dados alimentam pipeline de avaliação mensal |

---

### 2.3 Risco 2: Viés Algorítmico

#### Descrição do Risco
LLMs treinados em datasets predominantemente anglófonos e provenientes de países de alta renda tendem a apresentar viés em populações brasileiras, especialmente em:
- Populações negras e pardas (subdiagnóstico de doenças como lupus, hipertensão severa, anemia falciforme)
- Populações LGBTQIA+ (suposição implícita de heteronormatividade em anamnese)
- Idosos acima de 75 anos (polifarmácia e multimorbidade subrepresentadas nos datasets)
- Pacientes de baixa escolaridade (simplificação inadequada ou paternalismo na linguagem)

#### Mitigações Arquiteturais

| Mitigação | Descrição |
|---|---|
| **Auditoria de equidade (fairness audit)** | Avaliação trimestral com métricas disaggregadas por raça, gênero, faixa etária e região geográfica, usando ferramentas como IBM AI Fairness 360 ou Aequitas |
| **Dataset de fine-tuning nacional** | Parceria com hospitais universitários brasileiros para composição de dataset clinicamente representativo da população SUS e suplementar |
| **Prompt engineering inclusivo** | System prompts testados para eliminar suposições implícitas sobre identidade, corpo e comportamento do paciente |
| **Diversidade no painel de avaliadores** | Comitê de avaliação clínica com representação de médicas negras, endocrinologistas especializadas em diversidade e especialistas em saúde da população LGBTQIA+ |
| **Monitoramento de disparidade em tempo real** | Dashboard de observabilidade com alertas automáticos quando a taxa de rejeição de sugestões difere estatisticamente entre grupos demográficos |

---

### 2.4 Risco 3: Conformidade com a LGPD

#### Descrição do Risco
O BluaDiagnostics processa dados pessoais sensíveis de saúde — categoria máxima de proteção prevista na Lei nº 13.709/2018 (LGPD), Art. 11. Violações podem resultar em multas de até 2% do faturamento anual da Care Plus (limite de R$ 50 milhões por infração) e dano reputacional irreversível.

#### Mapeamento de Fluxos Sensíveis

```
Beneficiário → Operador → BluaDiagnostics → LLM Provider
     [CPF, CID, Rx]    [Prompt com PHI]   [Processamento externo?]
```

**O nó crítico é o envio de PHI (Protected Health Information) para APIs de LLM externas.**

#### Mitigações Arquiteturais

| Requisito LGPD | Mitigação Técnica |
|---|---|
| **Art. 7º — Base legal de tratamento** | Consentimento explícito e granular coletado no onboarding do beneficiário, com opção de opt-out a qualquer momento |
| **Art. 11º — Dados sensíveis de saúde** | Pseudonimização obrigatória antes do envio ao LLM: CPF → hash SHA-256 salted; nome → token reversível com chave HSM |
| **Art. 18º — Direitos do titular** | Portal self-service para acesso, correção, portabilidade e exclusão de dados; SLA de resposta de 15 dias úteis |
| **Art. 46º — Segurança** | Criptografia AES-256 em repouso, TLS 1.3 em trânsito; logs de auditoria imutáveis (WORM) por 5 anos |
| **Art. 50º — Governança de privacidade** | DPO formalmente designado; RIPD (Relatório de Impacto à Proteção de Dados) obrigatório antes do go-live |
| **Residência de dados** | Contrato de processamento de dados (DPA) com cláusula de residência em território nacional ou equivalência aprovada pela ANPD |
| **Retenção de prompts** | Política de zero-retention de prompts no provider de LLM; auditoria contratual semestral |

> **Recomendação crítica:** Avaliar deployment on-premise ou em nuvem privada nacional (ex.: OCI Brasil, AWS GovCloud equivalente nacional) para eliminar o risco de transferência internacional de dados de saúde.

---

### 2.5 Risco 4: Falha no Human-in-the-Loop

#### Descrição do Risco
O maior risco sistêmico não é o modelo errar — é o operador aceitar automaticamente sugestões do modelo sem revisão crítica ("automation bias"), transformando o BluaDiagnostics em um sistema de decisão autônoma não supervisionada.

#### Mitigações Arquiteturais

| Estratégia | Implementação |
|---|---|
| **UX de fricção intencional** | Interface projetada para exigir ação ativa do operador: sem botão de "aceitar tudo"; cada sugestão exige confirmação individual |
| **Explainability obrigatória** | Toda sugestão do modelo deve exibir o raciocínio clínico em linguagem compreensível (Chain-of-Thought visível) |
| **Alertas de alta complexidade** | Para casos com score de risco ≥ 8/10 (Manchester modificado), o sistema bloqueia encaminhamento e força escalada para médico supervisor |
| **Auditoria de aceitação** | Monitoramento da taxa de aceitação por operador; taxas acima de 95% disparam alerta de possível automação passiva |
| **Treinamento obrigatório** | Certificação trimestral obrigatória para operadores, com simulações de casos onde o modelo erra deliberadamente |
| **Responsabilidade legal clara** | Termos de uso que atribuem ao profissional de saúde a responsabilidade clínica pelas decisões, com o modelo como ferramenta de suporte |

---

## 3. Comparativo de Modelos LLM

### 3.1 Tabela Comparativa: GPT-4o vs. Claude Sonnet 4

| Critério | GPT-4o (OpenAI) | Claude Sonnet 4 (Anthropic) | Peso para BluaDiagnostics |
|---|---|---|---|
| **Latência média (p50)** | ~800ms | ~650ms | Alto — triagem requer resposta ágil |
| **Custo por 1M tokens (input)** | US$ 2,50 | US$ 3,00 | Médio — volume de uso previsível |
| **Custo por 1M tokens (output)** | US$ 10,00 | US$ 15,00 | Médio — outputs clínicos são curtos |
| **Janela de contexto máxima** | 128K tokens | 200K tokens | Alto — prontuários extensos e histórico de consultas |
| **Function calling / Tool use** | ✅ Nativo e maduro | ✅ Nativo e maduro | Alto — integração com PEP e APIs externas |
| **Deployment on-premise** | ❌ Não disponível | ⚠️ Mediante contrato Enterprise | Crítico — LGPD e dados de saúde |
| **Deployment em nuvem privada** | ⚠️ Azure OpenAI (limitado) | ⚠️ AWS/GCP via Bedrock/Vertex | Alto — isolamento de dados |
| **Raciocínio clínico (benchmarks médicos)** | MedQA: ~90% | MedQA: ~91% | Crítico — qualidade diagnóstica |
| **Suporte a português brasileiro** | Excelente | Excelente | Alto — interface em PT-BR |
| **Capacidade de recusa segura (safety)** | Alta | Muito Alta | Crítico — evitar prescrições perigosas |
| **Audit logging nativo** | Limitado | Limitado | Requer solução própria em ambos |
| **Conformidade SOC 2 Type II** | ✅ | ✅ | Necessário |
| **BAA para saúde (HIPAA/equivalente)** | ✅ Via Azure | ✅ Via AWS Bedrock | Crítico |

### 3.2 Análise Aprofundada por Critério

#### 3.2.1 Latência
O Claude Sonnet 4 apresenta latência ligeiramente inferior ao GPT-4o em cenários de streaming com outputs moderados (200–500 tokens), típicos de sugestões clínicas estruturadas. Para o BluaDiagnostics, onde o operador aguarda a resposta em tempo real durante o atendimento, cada segundo importa.

**Vantagem: Claude Sonnet 4**

#### 3.2.2 Custo por Token
O GPT-4o é mais econômico no output (US$ 10,00 vs. US$ 15,00/1M tokens). Considerando que os outputs clínicos do BluaDiagnostics serão relativamente curtos e estruturados (≤ 500 tokens por interação), o diferencial de custo em output será diluído. Projetando 10 milhões de interações/mês com média de 300 tokens de output cada, a diferença seria de aproximadamente US$ 15.000/mês — relevante, mas não determinante frente aos critérios clínicos e de privacidade.

**Vantagem: GPT-4o (custo de output)**

#### 3.2.3 Janela de Contexto
A janela de 200K tokens do Claude Sonnet 4 é decisiva para o contexto clínico: permite injetar o histórico completo do paciente, protocolos clínicos relevantes, resultados de exames e conversas anteriores em um único prompt, sem necessidade de sumarização com perda de informação.

**Vantagem: Claude Sonnet 4 (significativa)**

#### 3.2.4 Privacidade e Deployment On-Premise
Nenhum dos modelos oferece deployment on-premise nativo de forma simplificada. Contudo, o Claude Sonnet 4 está disponível via **Amazon Bedrock** com suporte a **Business Associate Agreement (BAA)** — equivalente funcional ao exigido pela HIPAA e alinhável à LGPD — e com possibilidade de VPC isolation completa, garantindo que nenhum dado trafegue fora da infraestrutura da Care Plus. O GPT-4o oferece caminho similar via Azure OpenAI, mas com maior latência adicional e custos de egresso mais elevados no contexto nacional.

**Vantagem: Claude Sonnet 4 (via AWS Bedrock com menor latência de rede no Brasil)**

#### 3.2.5 Function Calling e Integração
Ambos os modelos possuem suporte maduro a function calling, essencial para:
- Consultar o prontuário eletrônico (PEP) via API REST
- Checar bula de medicamentos na base ANVISA
- Acionar fluxo de agendamento de teleconsulta
- Gravar estruturado no sistema de triagem

O **Claude Sonnet 4** possui schema de tool use que facilita o encadeamento de múltiplas ferramentas em fluxos clínicos complexos, com menor taxa de erros de parsing observada em benchmarks de uso de ferramentas.

**Vantagem: Claude Sonnet 4 (marginal)**

#### 3.2.6 Raciocínio Clínico e Recusa Segura
Em benchmarks médicos especializados (MedQA, MedMCQA, USMLE), ambos os modelos performam em nível próximo ao especialista. No entanto, o Claude Sonnet 4 demonstra comportamento mais conservador e calibrado na recusa de solicitações clinicamente perigosas — como geração de prescrições sem dados suficientes ou orientações contraindicadas — o que é uma vantagem crítica no contexto do BluaDiagnostics.

**Vantagem: Claude Sonnet 4**

---

### 3.3 Decisão Final: Modelo Selecionado

## ✅ Claude Sonnet 4 (Anthropic) — via Amazon Bedrock

**Justificativa consolidada:**

O Claude Sonnet 4 supera o GPT-4o nos critérios de maior peso para o BluaDiagnostics:

1. **Segurança clínica superior** — comportamento mais conservador e calibrado em recusas perigosas, reduzindo o risco de alucinações com consequências clínicas graves.
2. **Janela de contexto de 200K tokens** — permite ingestão completa de prontuários sem truncamento, preservando o contexto clínico integral do paciente.
3. **Privacidade robusta via AWS Bedrock** — VPC isolation, BAA disponível, dados processados em região Brasil (sa-east-1), alinhamento mais direto com as exigências da LGPD.
4. **Latência competitiva** — desempenho ligeiramente superior no P50, relevante para a experiência do operador em tempo real.
5. **Custo aceitável** — o diferencial de custo de output (~US$ 5/1M tokens) é justificado pela superioridade nos critérios clínicos e regulatórios.

> **Nota importante:** A decisão de modelo deve ser revisada semestralmente, considerando a velocidade de evolução do ecossistema de LLMs. O BluaDiagnostics deve ser arquitetado com uma **camada de abstração de modelo (LLM Gateway)** — ex.: LiteLLM ou Portkey — que permita troca de provider sem refatoração do backend clínico.

---

## 4. Arquitetura de Alto Nível (Síntese)

```
┌─────────────────────────────────────────────────────────────────────┐
│                        BLUA DIAGNOSTICS                             │
│                    Arquitetura de Referência                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  [Operador de Triagem]                                              │
│         │                                                           │
│         ▼                                                           │
│  ┌─────────────────┐    ┌──────────────────────────────────────┐   │
│  │  Interface Web  │───▶│         API Gateway (HTTPS/TLS 1.3)  │   │
│  │  (React + PEP)  │    └──────────────────────────────────────┘   │
│  └─────────────────┘                      │                        │
│                                           ▼                        │
│                            ┌─────────────────────────┐            │
│                            │   Orchestration Layer   │            │
│                            │  (LangGraph + LiteLLM)  │            │
│                            └─────────────────────────┘            │
│                              │           │           │             │
│                 ┌────────────┘    ┌──────┘   ┌──────┘             │
│                 ▼                 ▼           ▼                    │
│          ┌──────────┐    ┌──────────────┐ ┌──────────────┐        │
│          │ RAG Engine│    │ Guardrails   │ │ Claude Sonnet│        │
│          │(Weaviate) │    │ (NLP + Rules)│ │ 4 via Bedrock│        │
│          └──────────┘    └──────────────┘ └──────────────┘        │
│                 │                 │                                │
│                 ▼                 ▼                                │
│          ┌─────────────────────────────────────────────┐          │
│          │         Bases de Conhecimento Clínico        │          │
│          │  ANVISA | CID-11 | SNOMED CT | Protocolos   │          │
│          │         Care Plus | Micromedex | RxNorm      │          │
│          └─────────────────────────────────────────────┘          │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                 Camada de Observabilidade                    │   │
│  │    Logging imutável | Fairness dashboard | Audit trail       │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 5. Conclusão e Próximos Passos

| Fase | Entregável | Prazo Sugerido |
|---|---|---|
| **Fase 0** | RIPD + DPO designado + Contrato DPA com Anthropic/AWS | Mês 1 |
| **Fase 1** | MVP com RAG sobre protocolos Care Plus + interface do operador | Mês 2–3 |
| **Fase 2** | Integração com PEP + function calling para agendamento | Mês 4–5 |
| **Fase 3** | Auditoria de equidade + fine-tuning com dataset nacional | Mês 6–8 |
| **Fase 4** | Expansão para paciente final (com validação regulatória CFM) | Mês 9–12 |

> **O BluaDiagnostics não é um substituto do médico. É a amplificação da capacidade humana de cuidar — com velocidade, consistência e empatia escalável.**

---

*Documento elaborado conforme as diretrizes da LGPD (Lei nº 13.709/2018), Resolução CFM nº 2.314/2022, recomendações da ANPD e melhores práticas de IA responsável em saúde.*
