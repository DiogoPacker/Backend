from django.core.validators import EmailValidator, MinValueValidator
from django.db import models

from rentals.models import Rental


class CustomerRewards(models.Model):
    """
    Saldo de pontos de recompensa de um cliente.

    Vinculado ao e-mail do cliente (mesmo identificador usado em Rental) para
    evitar a criação de um modelo Customer separado e manter retrocompatibilidade
    com o sistema existente.

    Separamos 'total_points' (saldo atual) de 'lifetime_points_earned' (total
    acumulado ao longo de toda a vida do cliente) para permitir relatórios
    precisos independentemente de resgates.
    """

    TIER_CHOICES = [
        ("Bronze", "Bronze"),
        ("Silver", "Silver"),
        ("Gold", "Gold"),
    ]

    customer_email = models.EmailField(
        unique=True,
        validators=[EmailValidator()],
        db_index=True,
    )
    total_points = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Saldo atual de pontos disponíveis para resgate.",
    )
    tier = models.CharField(max_length=10, choices=TIER_CHOICES, default="Bronze")
    lifetime_points_earned = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Total de pontos ganhos ao longo de todas as locações.",
    )
    lifetime_points_redeemed = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Total de pontos resgatados ao longo de toda a vida do cliente.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "customer_rewards"
        verbose_name = "Customer Rewards"
        verbose_name_plural = "Customer Rewards"

    def __str__(self):
        return f"{self.customer_email} — {self.total_points} pts ({self.tier})"


class RewardTransaction(models.Model):
    """
    Registro auditável de cada movimentação de pontos.

    Cada concessão ou resgate gera uma linha aqui, permitindo rastrear
    exatamente quais locações geraram quais pontos e quando.
    A FK para Rental usa SET_NULL para que o histórico não se perca
    caso uma locação seja removida.
    """

    TRANSACTION_TYPE_CHOICES = [
        ("earned", "Earned"),
        ("redeemed", "Redeemed"),
    ]

    customer_rewards = models.ForeignKey(
        CustomerRewards,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    rental = models.ForeignKey(
        Rental,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reward_transactions",
        help_text="Locação que originou esta transação, se aplicável.",
    )
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPE_CHOICES)
    points = models.IntegerField(
        help_text="Pontos ganhos (positivo) ou resgatados (negativo).",
    )
    reason = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "reward_transactions"
        ordering = ["-created_at"]
        verbose_name = "Reward Transaction"
        verbose_name_plural = "Reward Transactions"

    def __str__(self):
        return f"{self.transaction_type} {self.points} pts — {self.customer_rewards.customer_email}"
