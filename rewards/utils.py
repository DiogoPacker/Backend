"""
Lógica de negócio do sistema de recompensas.

Funções puras sem dependência de banco de dados — recebem dados e retornam
valores. Isso permite testá-las sem banco e sem request HTTP.
"""

from decimal import Decimal, ROUND_HALF_UP

from rentals.models import Rental


# ---------------------------------------------------------------------------
# Constantes de negócio
# ---------------------------------------------------------------------------

# Limites de diária que definem a categoria do veículo
_DAILY_RATE_STANDARD_MIN = Decimal("300")
_DAILY_RATE_PREMIUM_MIN = Decimal("500")

# Pontos base por dia de locação
BASE_POINTS_PER_DAY = 10

# Bônus de categoria por dia de locação
CATEGORY_BONUS_PER_DAY: dict[str, int] = {
    "economy": 0,
    "standard": 5,
    "premium": 10,
}

# Bônus por duração da locação (aplicado apenas o maior nível elegível)
DURATION_BONUS: dict[int, int] = {
    14: 150,
    7: 50,
}

# Bônus por devolução pontual
ON_TIME_RETURN_BONUS = 25

# Limites de pontos por nível (tier)
TIER_THRESHOLDS: dict[str, int] = {
    "Gold": 1000,
    "Silver": 500,
    "Bronze": 0,
}

# Multiplicadores por nível
TIER_MULTIPLIERS: dict[str, Decimal] = {
    "Gold": Decimal("1.5"),
    "Silver": Decimal("1.25"),
    "Bronze": Decimal("1.0"),
}


# ---------------------------------------------------------------------------
# Funções de suporte
# ---------------------------------------------------------------------------

def get_car_category(daily_rate: Decimal) -> str:
    """
    Determinar a categoria do carro com base na diária.

    Args:
        daily_rate: Valor da diária do carro

    Returns:
        'economy', 'standard' ou 'premium'
    """
    daily_rate = Decimal(str(daily_rate))
    if daily_rate >= _DAILY_RATE_PREMIUM_MIN:
        return "premium"
    if daily_rate >= _DAILY_RATE_STANDARD_MIN:
        return "standard"
    return "economy"


def get_customer_tier(total_points: int) -> str:
    """
    Determinar o nível (tier) do cliente com base nos pontos acumulados.

    Args:
        total_points: Saldo atual de pontos do cliente

    Returns:
        'Bronze', 'Silver' ou 'Gold'
    """
    if total_points >= TIER_THRESHOLDS["Gold"]:
        return "Gold"
    if total_points >= TIER_THRESHOLDS["Silver"]:
        return "Silver"
    return "Bronze"


def get_points_to_next_tier(total_points: int) -> int | None:
    """
    Calcular quantos pontos faltam para o próximo nível.

    Returns:
        Pontos necessários, ou None se já estiver no nível máximo (Gold).
    """
    if total_points >= TIER_THRESHOLDS["Gold"]:
        return None
    if total_points >= TIER_THRESHOLDS["Silver"]:
        return TIER_THRESHOLDS["Gold"] - total_points
    return TIER_THRESHOLDS["Silver"] - total_points


def calculate_rental_points(rental: Rental, current_tier: str = "Bronze") -> int:
    """
    Calcular os pontos a conceder ao cliente pela devolução de uma locação.

    Regras aplicadas (em ordem):
    1. 10 pontos base por dia
    2. Bônus de categoria por dia (Economy: +0, Standard: +5, Premium: +10)
    3. Bônus de duração — apenas o maior nível elegível:
       14+ dias: +150 pts | 7+ dias: +50 pts
    4. Bônus de devolução pontual (+25 se devolvido na data ou antes)
    5. Multiplicador de nível (Bronze 1×, Silver 1.25×, Gold 1.5×)
       aplicado sobre o subtotal, resultado arredondado (ROUND_HALF_UP)

    Args:
        rental:       Instância do modelo Rental com actual_return_date preenchido
        current_tier: Nível atual do cliente antes desta transação

    Returns:
        Total de pontos a creditar (inteiro, mínimo 0)
    """
    rental_days = max((rental.end_date - rental.start_date).days, 1)
    category = get_car_category(rental.car.daily_rate)

    # 1 + 2: pontos base + bônus de categoria por dia
    daily_points = BASE_POINTS_PER_DAY + CATEGORY_BONUS_PER_DAY[category]
    points = daily_points * rental_days

    # 3: bônus de duração — apenas o maior nível aplicável
    for threshold in sorted(DURATION_BONUS.keys(), reverse=True):
        if rental_days >= threshold:
            points += DURATION_BONUS[threshold]
            break

    # 4: bônus de devolução pontual
    if rental.actual_return_date and rental.actual_return_date <= rental.end_date:
        points += ON_TIME_RETURN_BONUS

    # 5: multiplicador de nível
    multiplier = TIER_MULTIPLIERS.get(current_tier, Decimal("1.0"))
    total = Decimal(str(points)) * multiplier
    return int(total.to_integral_value(rounding=ROUND_HALF_UP))
