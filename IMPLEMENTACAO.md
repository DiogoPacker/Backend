# Implementação — Sistema de Recompensas para Clientes

> Branch: `feature/rewards-system`

---

## 1. Visão Geral

Foi implementado um **sistema completo de pontos de recompensa** integrado ao fluxo de locação existente. O sistema cobre todos os requisitos obrigatórios e recomendados do FEATURE.md:

- Crédito automático de pontos na devolução de carros
- Sistema de níveis (Bronze / Silver / Gold) com multiplicadores
- Endpoints REST para consulta de saldo, histórico e resgate
- Painel admin completo
- Correção de bugs e refatorações no código original
- 119 testes automatizados, todos passando

---

## 2. Arquitetura

### Novo módulo Django: `rewards/`

O sistema foi implementado como um **app Django independente** (`rewards/`), separado de `rentals/`. Isso garante:

- Coesão: toda a lógica de recompensas fica num único lugar
- Baixo acoplamento: `rentals/` importa de `rewards/` apenas para crédito de pontos; o restante é independente
- Escalabilidade: o app pode ser extraído para um serviço separado no futuro sem refatoração profunda

### Estrutura de camadas (padrão existente, mantido e expandido)

```
rewards/
├── models.py       — CustomerRewards + RewardTransaction (banco de dados)
├── database.py     — camada de acesso a dados (único lugar que toca o ORM)
├── utils.py        — lógica pura de negócio (sem dependência de banco)
├── serializers.py  — contratos da API
├── views.py        — orquestração HTTP
├── urls.py         — rotas do app
├── admin.py        — painel de administração
└── migrations/     — migrações independentes de rentals/
```

### Modelos de banco de dados

**`CustomerRewards`** — agregado de saldo por cliente
- Vinculado ao `customer_email` (mesmo identificador de `Rental`), evitando quebrar retrocompatibilidade
- Dois contadores separados: `total_points` (saldo disponível) e `lifetime_points_earned` (total histórico). Essa separação permite relatórios precisos mesmo após resgates — se fossem o mesmo campo, seria impossível saber o total ganho após um débito
- `tier` atualizado automaticamente na camada de banco a cada crédito/débito

**`RewardTransaction`** — log imutável de cada movimentação
- FK para `Rental` com `on_delete=SET_NULL`: o histórico de pontos sobrevive mesmo se a locação for removida
- Cada operação (crédito ou resgate) cria um registro com tipo, valor, motivo e timestamp — trilha de auditoria completa

### Integração com `return_rental`

O crédito de pontos é acionado no final de `return_rental`, **após** todas as operações já existentes (multa, atualização de carro, persistência). A resposta do endpoint foi estendida com a chave `"rewards"` — os campos originais não foram alterados, mantendo retrocompatibilidade.

### Atomicidade

`add_earned_points` e `redeem_points` são decoradas com `@transaction.atomic`. Isso garante que a atualização do saldo e a criação do `RewardTransaction` ocorrem juntas — ou ambas persistem, ou nenhuma.

---

## 3. Correções de Bugs no Código Original

| # | Arquivo | Descrição do Bug | Correção Aplicada |
|---|---------|------------------|-------------------|
| 1 | `rentals/views.py` | `total_cost = daily_rate * days` — variável `daily_rate` nunca definida → `NameError` em produção | Substituído por `car.daily_rate` |
| 2 | `rentals/views.py` | `car = rental.car` definido **dentro** do `if late_fee` mas usado **fora** para marcar carro disponível → `NameError` em devoluções pontuais | Movido para antes do bloco condicional |
| 3 | `rentals/utils.py` | `validate_rental_dates()` era um stub vazio (`pass`) — aceitava silenciosamente datas inválidas (fim antes do início, datas nulas) | Implementada com validação real |
| 4 | `rentals/tests.py` | Métodos de teste **sem prefixo `test_`** — nunca eram executados pelo runner | Todos renomeados e expandidos |

---

## 4. Refatorações (Qualidade e Performance)

| Arquivo | Problema Original | Solução |
|---------|-------------------|----------|
| `rentals/database.py` — `get_available_cars()` | Loop Python sobre **todos** os carros para filtrar disponíveis — O(n) em memória | `.filter(available=True)` — filtro delegado ao banco |
| `rentals/database.py` — `get_customer_rentals()` | Loop Python sobre **todas** as locações para filtrar por e-mail | `.filter(customer_email=email)` |
| `rentals/database.py` — `get_rental_stats()` | Três loops Python para contar e somar dados | `.aggregate(Count, Sum)` — uma única query |
| `rentals/database.py` — `get_rental_by_id()` | Sem `select_related` — acessar `rental.car` disparava query extra (N+1) | `select_related('car')` adicionado |
| `rentals/database.py` — `get_all_rentals()` | Mesmo problema de N+1 | `select_related('car')` adicionado |

---

## 5. Regras de Negócio Implementadas

### Cálculo de Pontos (`rewards/utils.py: calculate_rental_points`)

As regras são aplicadas **nesta ordem**:

1. **Pontos base**: 10 pts × nº de dias
2. **Bônus de categoria** (por dia):
   - Economy (diária < R$ 300): +0 pts/dia
   - Standard (R$ 300–499): +5 pts/dia
   - Premium (R$ 500+): +10 pts/dia
3. **Bônus de duração** — apenas o maior nível aplicável (não cumulativos):
   - 14+ dias: +150 pts
   - 7+ dias: +50 pts
4. **Bônus de pontualidade**: +25 pts se devolvido na data ou antes
5. **Multiplicador de nível** aplicado sobre o subtotal (arredondamento `ROUND_HALF_UP`):
   - Bronze: ×1.0
   - Silver: ×1.25
   - Gold: ×1.5

**Verificação do cenário do FEATURE.md:**

> Audi Q3 (R$ 600/dia), 8 dias, devolução pontual, Bronze:
> `(10 + 10) × 8 + 50 + 25 = 235 pts`
>
> Com Silver: `235 × 1.25 = 293.75 → 294 pts` (ROUND_HALF_UP)

### Resgate de Pontos

- 100 pontos = R$ 50 de desconto
- Resgate mínimo: 100 pontos
- Saldo insuficiente retorna erro descritivo (sem alterar o banco)
- Apenas o dono da locação pode resgatar pontos nela (`403` caso contrário)

---

## 6. Endpoints da API

| Método | URL | Descrição |
|--------|-----|----------|
| `GET` | `/api/cars/` | Listar carros disponíveis |
| `GET` | `/api/cars/<id>/` | Detalhe de um carro |
| `POST` | `/api/rentals/create/` | Criar locação |
| `POST` | `/api/rentals/<id>/return/` | Devolver carro (credita pontos automaticamente) |
| `GET` | `/api/rentals/` | Listar todas as locações |
| `GET` | `/api/rentals/customer/<email>/` | Locações de um cliente |
| `GET` | `/api/stats/` | Estatísticas gerais |
| `GET` | `/api/rewards/customer/<email>/` | Saldo e nível do cliente |
| `GET` | `/api/rewards/customer/<email>/history/` | Histórico de transações |
| `POST` | `/api/rewards/apply/` | Resgatar pontos como desconto |

---

## 7. Testes

**119 testes, 0 falhas.** Tempo de execução: ~1.5s.

### Distribuição por arquivo e classe

| Arquivo | Classe | Nº de Testes | O que cobre |
|---------|--------|:---:|-------------|
| `rentals/tests.py` | `CarAPITestCase` | 5 | Listar, detalhar, 404 |
| `rentals/tests.py` | `RentalAPITestCase` | 18 | Criar, devolver, desconto, multa, consultas |
| `rentals/tests.py` | `DatabaseLayerTests` | 9 | ORM queries, select_related, stats, revenue |
| `rentals/tests.py` | `UtilsTests` | 10 | validate_rental_dates, calculate_discount, calculate_late_fee |
| `rewards/tests.py` | `RewardCalculationTests` | 29 | Cada regra isolada: categoria, tier, pontos_to_next, cenários FEATURE.md |
| `rewards/tests.py` | `RewardDatabaseTests` | 14 | get_or_create, add_earned, redeem, atomicidade, histórico |
| `rewards/tests.py` | `RewardAPITestCase` | 34 | Todos os endpoints, casos de erro, upgrade de tier |

### Estratégia

- **Testes unitários** (`RewardCalculationTests`): funções puras sem HTTP, isolam cada regra de negócio individualmente — rápidos e fáceis de manter
- **Testes de camada de dados** (`DatabaseLayerTests`, `RewardDatabaseTests`): validam ORM, atomicidade e integridade sem passar pela camada HTTP
- **Testes de integração** (`RewardAPITestCase`, `RentalAPITestCase`): percorrem o fluxo completo via `APIClient`, verificando status codes, payload e efeitos colaterais no banco

---

## 8. Como Executar

### Pré-requisitos

```bash
pip install -r requirements.txt
python manage.py migrate
```

### Suite de testes automatizados

```bash
# Todos os testes (rentals + rewards)
python manage.py test rentals rewards --verbosity=2

# Apenas rewards
python manage.py test rewards --verbosity=2

# Com pytest
pytest
```

Resultado esperado: **119 passed, 0 failed**.

### Servidor local

```bash
python manage.py runserver
# API disponível em http://localhost:8000/api/
```

### Fluxo completo (happy path)

```bash
# 1. Listar carros
curl http://localhost:8000/api/cars/

# 2. Criar locação de carro premium por 8 dias
curl -X POST http://localhost:8000/api/rentals/create/ \
  -H "Content-Type: application/json" \
  -d '{"car_id": 1, "customer_name": "João Silva", "customer_email": "joao@example.com", "days": 8}'

# 3. Devolver o carro (id da locação retornado acima)
# Resposta já inclui pontos ganhos no campo "rewards"
curl -X POST http://localhost:8000/api/rentals/1/return/

# 4. Consultar saldo (235 pts para carro premium 8 dias Bronze)
curl http://localhost:8000/api/rewards/customer/joao@example.com/

# 5. Histórico de transações
curl http://localhost:8000/api/rewards/customer/joao@example.com/history/

# 6. Resgatar 100 pontos (= R$50 de desconto)
curl -X POST http://localhost:8000/api/rewards/apply/ \
  -H "Content-Type: application/json" \
  -d '{"rental_id": 1, "customer_email": "joao@example.com", "points_to_redeem": 100}'
```

### Admin Django

```bash
python manage.py createsuperuser
# Acesse http://localhost:8000/admin/
```

Seções disponíveis: **Cars**, **Rentals**, **Customer Rewards**, **Reward Transactions**.

### Carga de dados de exemplo

```bash
python init_data.py
```

---

## 9. Suposições Documentadas

1. **Bônus de duração são exclusivos, não cumulativos**: uma locação de 14+ dias recebe +150 pts, não +200 pts (50+150). O FEATURE.md lista os dois separados sem especificar acumulação; o exemplo de 8 dias (que recebe apenas +50) reforça a interpretação de "apenas o maior nível aplicável".

2. **Nomes dos tiers em inglês** (`Bronze/Silver/Gold`): o FEATURE.md usa "Prata/Ouro" no texto em português, mas o exemplo JSON mostra `"tier": "Bronze"`. Optei por inglês para consistência com o campo `choices` do modelo Django.

3. **Pontos sempre concedidos mesmo em devoluções com atraso**: o spec diz "sem bônus de pontualidade" para devoluções atrasadas, mas não proíbe os demais pontos. Implementado: base + categoria + duração sempre creditados; o +25 de pontualidade só é adicionado se devolvido na data ou antes.

4. **`GET /api/rewards/customer/<email>/` retorna 404 antes da primeira devolução**: o registro `CustomerRewards` é criado automaticamente na primeira devolução. Antes disso, o endpoint retorna 404 em vez de um objeto com 0 pontos — evita criar registros fantasmas para e-mails que nunca alugaram.

5. **Resgate não aplica desconto financeiro automaticamente na `Rental`**: o endpoint `POST /api/rewards/apply/` deduz os pontos e informa o `discount_brl`, mas aplicar esse valor ao custo da locação é responsabilidade do front-end — o modelo `Rental` atual não tem campo de desconto aplicado.

---

## 10. O Que Melhoraria com Mais Tempo

- **Paginação no histórico**: com muitas locações, `/history/` pode retornar centenas de registros. Adicionaria `PageNumberPagination` do DRF.
- **Modelo `Customer` com UUID**: vincular ao e-mail é frágil — um cliente que muda o e-mail perde o histórico. Um `Customer` com UUID resolveria isso de forma robusta.
- **Índice composto em `RewardTransaction`**: `(customer_rewards_id, created_at)` para queries de histórico com filtro por data.
- **Expiração de pontos**: em programas reais, pontos têm validade. Adicionaria `expires_at` em `RewardTransaction` e uma tarefa Celery periódica.
- **Testes de concorrência**: o `@transaction.atomic` protege contra race conditions simples, mas testes com threads paralelas validariam o comportamento sob carga real.
- **Exportação de histórico**: CSV/PDF mencionado como opcional no FEATURE.md.

