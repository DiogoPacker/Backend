from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from rentals import database as rental_db
from . import database
from .serializers import (
    CustomerRewardsSerializer,
    RedeemPointsSerializer,
    RewardTransactionSerializer,
)


@api_view(["GET"])
def get_customer_rewards(request, customer_email):
    """
    Retornar o saldo de recompensas e nível (tier) atual do cliente.

    GET /api/rewards/customer/{customer_email}/

    Se o cliente ainda não possui um registro de recompensas, retorna 404 —
    o registro é criado automaticamente na primeira devolução de carro.
    """
    rewards = database.get_customer_rewards(customer_email)
    if rewards is None:
        return Response(
            {"error": "Nenhum registro de recompensas encontrado para este cliente."},
            status=status.HTTP_404_NOT_FOUND,
        )
    serializer = CustomerRewardsSerializer(rewards)
    return Response(serializer.data)


@api_view(["GET"])
def get_rewards_history(request, customer_email):
    """
    Retornar o histórico completo de transações de pontos do cliente.

    GET /api/rewards/customer/{customer_email}/history/
    """
    rewards = database.get_customer_rewards(customer_email)
    if rewards is None:
        return Response(
            {"error": "Nenhum registro de recompensas encontrado para este cliente."},
            status=status.HTTP_404_NOT_FOUND,
        )
    transactions = database.get_reward_transactions(customer_email)
    serializer = RewardTransactionSerializer(transactions, many=True)
    return Response({
        "customer_email": customer_email,
        "transactions": serializer.data,
    })


@api_view(["POST"])
def apply_rewards(request):
    """
    Resgatar pontos do cliente como desconto em uma locação.

    POST /api/rewards/apply/

    Regras de negócio:
    - Mínimo de 100 pontos por resgate
    - A locação deve existir e pertencer ao cliente informado
    - O cliente deve ter saldo suficiente
    - Cada 100 pontos equivalem a R$50 de desconto
    """
    serializer = RedeemPointsSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    data = serializer.validated_data

    rental = rental_db.get_rental_by_id(data["rental_id"])
    if rental is None:
        return Response(
            {"error": "Locação não encontrada."},
            status=status.HTTP_404_NOT_FOUND,
        )

    if rental.customer_email != data["customer_email"]:
        return Response(
            {"error": "Esta locação não pertence ao cliente informado."},
            status=status.HTTP_403_FORBIDDEN,
        )

    rewards = database.get_customer_rewards(data["customer_email"])
    if rewards is None:
        return Response(
            {"error": "Nenhum registro de recompensas encontrado para este cliente."},
            status=status.HTTP_404_NOT_FOUND,
        )

    points_to_redeem = data["points_to_redeem"]
    transaction_record, error = database.redeem_points(rewards, rental, points_to_redeem)
    if error:
        return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)

    discount_value = (points_to_redeem // 100) * 50
    return Response({
        "message": f"Resgate realizado com sucesso. Desconto de R${discount_value:.2f} aplicado.",
        "points_redeemed": points_to_redeem,
        "remaining_points": rewards.total_points,
        "tier": rewards.tier,
        "discount_brl": discount_value,
    })
