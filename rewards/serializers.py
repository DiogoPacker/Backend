from rest_framework import serializers

from .models import CustomerRewards, RewardTransaction
from .utils import get_points_to_next_tier


class CustomerRewardsSerializer(serializers.ModelSerializer):
    """Serializer para exibir o saldo e nível de recompensas de um cliente."""

    points_to_next_tier = serializers.SerializerMethodField()

    class Meta:
        model = CustomerRewards
        fields = [
            "customer_email",
            "total_points",
            "tier",
            "points_to_next_tier",
            "lifetime_points_earned",
            "lifetime_points_redeemed",
        ]

    def get_points_to_next_tier(self, obj) -> int | None:
        return get_points_to_next_tier(obj.total_points)


class RewardTransactionSerializer(serializers.ModelSerializer):
    """Serializer para uma transação individual no histórico de pontos."""

    type = serializers.CharField(source="transaction_type")
    rental_id = serializers.PrimaryKeyRelatedField(source="rental", read_only=True)
    timestamp = serializers.DateTimeField(source="created_at")

    class Meta:
        model = RewardTransaction
        fields = ["id", "type", "points", "reason", "rental_id", "timestamp"]


class RedeemPointsSerializer(serializers.Serializer):
    """Serializer para validar o payload de resgate de pontos."""

    rental_id = serializers.IntegerField()
    customer_email = serializers.EmailField()
    points_to_redeem = serializers.IntegerField(min_value=100)
