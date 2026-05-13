"""
Camada de acesso a dados do módulo de recompensas.

Toda interação com banco de dados referente a CustomerRewards e
RewardTransaction passa por aqui — as views e utils nunca tocam o ORM
diretamente, mantendo a separação de responsabilidades.
"""

from django.db import transaction

from rentals.models import Rental
from .models import CustomerRewards, RewardTransaction


def get_or_create_customer_rewards(customer_email: str) -> CustomerRewards:
    """
    Retornar o saldo de recompensas do cliente, criando-o se ainda não existir.

    Utiliza get_or_create para garantir atomicidade e evitar condição de corrida
    caso dois requests simultâneos tentem criar o mesmo registro.
    """
    rewards, _ = CustomerRewards.objects.get_or_create(
        customer_email=customer_email,
        defaults={
            "total_points": 0,
            "lifetime_points_earned": 0,
            "lifetime_points_redeemed": 0,
        },
    )
    return rewards


def get_customer_rewards(customer_email: str) -> CustomerRewards | None:
    """Retornar o saldo de recompensas do cliente ou None se não existir."""
    try:
        return CustomerRewards.objects.get(customer_email=customer_email)
    except CustomerRewards.DoesNotExist:
        return None


def get_reward_transactions(
    customer_email: str,
    transaction_type: str | None = None,
    ordering: str = "-created_at",
):
    """
    Retornar o histórico de transações do cliente.

    Args:
        customer_email:   E-mail do cliente.
        transaction_type: Filtrar por 'earned' ou 'redeemed'. None retorna todos.
        ordering:         Campo de ordenação ORM (ex: '-created_at', 'created_at').

    Usa select_related para evitar query N+1 ao acessar customer_rewards e rental.
    """
    qs = (
        RewardTransaction.objects
        .select_related("customer_rewards", "rental")
        .filter(customer_rewards__customer_email=customer_email)
    )
    if transaction_type:
        qs = qs.filter(transaction_type=transaction_type)
    return qs.order_by(ordering)


@transaction.atomic
def add_earned_points(
    rewards: CustomerRewards,
    rental: Rental,
    points: int,
    reason: str,
) -> RewardTransaction:
    """
    Creditar pontos ao cliente e registrar a transação de forma atômica.

    O decorador @transaction.atomic garante que a atualização do saldo e a
    criação da transação ocorrem juntas — ou ambas persistem, ou nenhuma.

    Args:
        rewards: Instância de CustomerRewards do cliente
        rental:  Locação que originou os pontos
        points:  Quantidade de pontos a creditar
        reason:  Descrição legível do motivo

    Returns:
        A RewardTransaction criada
    """
    from .utils import get_customer_tier

    rewards.total_points += points
    rewards.lifetime_points_earned += points
    rewards.tier = get_customer_tier(rewards.total_points)
    rewards.save()

    return RewardTransaction.objects.create(
        customer_rewards=rewards,
        rental=rental,
        transaction_type="earned",
        points=points,
        reason=reason,
    )


@transaction.atomic
def redeem_points(
    rewards: CustomerRewards,
    rental: Rental,
    points: int,
) -> tuple[RewardTransaction | None, str]:
    """
    Deduzir pontos do saldo do cliente para resgate de desconto.

    Returns:
        Tupla (RewardTransaction, error_message). Se error_message for vazio,
        a operação foi bem-sucedida.
    """
    from .utils import get_customer_tier

    if points <= 0:
        return None, "O número de pontos a resgatar deve ser maior que zero."
    if points < 100:
        return None, "O resgate mínimo é de 100 pontos."
    if rewards.total_points < points:
        return None, (
            f"Saldo insuficiente. Disponível: {rewards.total_points} pts, "
            f"solicitado: {points} pts."
        )

    rewards.total_points -= points
    rewards.lifetime_points_redeemed += points
    rewards.tier = get_customer_tier(rewards.total_points)
    rewards.save()

    transaction_record = RewardTransaction.objects.create(
        customer_rewards=rewards,
        rental=rental,
        transaction_type="redeemed",
        points=-points,
        reason=(
            f"Resgate de {points} pontos na locação #{rental.id} "
            f"— desconto de R${points // 2:.2f}"
        ),
    )
    return transaction_record, ""
