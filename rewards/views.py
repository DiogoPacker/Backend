import csv
import io

from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.http import HttpResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle
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

# Mapeamento de parâmetros públicos da API para campos ORM.
# Expõe 'timestamp' em vez de 'created_at' para manter consistência com
# o nome do campo no serializer.
_ORDERING_MAP = {
    "timestamp": "created_at",
    "-timestamp": "-created_at",
}
_VALID_TYPES = {"earned", "redeemed"}
_DEFAULT_PAGE_SIZE = 20
_MAX_PAGE_SIZE = 100


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
    Retornar o histórico de transações de pontos do cliente com suporte a
    filtragem, ordenação e paginação.

    GET /api/rewards/customer/{customer_email}/history/

    Query params:
      type      — filtrar por tipo: 'earned' ou 'redeemed'
      ordering  — ordenar por: 'timestamp' (asc) ou '-timestamp' (desc, padrão)
      page      — número da página (padrão: 1)
      page_size — itens por página (padrão: 20, máximo: 100)
    """
    rewards = database.get_customer_rewards(customer_email)
    if rewards is None:
        return Response(
            {"error": "Nenhum registro de recompensas encontrado para este cliente."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # --- filtragem por tipo ---
    transaction_type = request.query_params.get("type")
    if transaction_type and transaction_type not in _VALID_TYPES:
        return Response(
            {"error": f"Tipo inválido. Use: {', '.join(sorted(_VALID_TYPES))}."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # --- ordenação ---
    ordering_param = request.query_params.get("ordering", "-timestamp")
    ordering = _ORDERING_MAP.get(ordering_param, "-created_at")

    transactions = database.get_reward_transactions(customer_email, transaction_type, ordering)

    # --- paginação ---
    try:
        page_size = min(int(request.query_params.get("page_size", _DEFAULT_PAGE_SIZE)), _MAX_PAGE_SIZE)
        if page_size < 1:
            page_size = _DEFAULT_PAGE_SIZE
    except (ValueError, TypeError):
        page_size = _DEFAULT_PAGE_SIZE

    paginator = Paginator(transactions, page_size)
    try:
        page = paginator.page(int(request.query_params.get("page", 1)))
    except (EmptyPage, PageNotAnInteger, ValueError):
        page = paginator.page(paginator.num_pages or 1)

    serializer = RewardTransactionSerializer(page.object_list, many=True)
    return Response({
        "customer_email": customer_email,
        "count": paginator.count,
        "total_pages": paginator.num_pages,
        "page": page.number,
        "page_size": page_size,
        "transactions": serializer.data,
    })


_VALID_FORMATS = {"csv", "pdf"}
_CSV_HEADERS = ["id", "type", "points", "reason", "rental_id", "timestamp"]


def _build_csv_response(customer_email, transactions):
    """Constrói um HttpResponse com o histórico serializado como CSV."""
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="rewards_{customer_email}.csv"'
    )
    writer = csv.writer(response)
    writer.writerow(_CSV_HEADERS)
    for tx in transactions:
        writer.writerow([
            tx.id,
            tx.transaction_type,
            tx.points,
            tx.reason,
            tx.rental_id,
            tx.created_at.isoformat(),
        ])
    return response


def _build_pdf_response(customer_email, transactions, rewards):
    """Constrói um HttpResponse com o histórico serializado como PDF."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=40,
        rightMargin=40,
        topMargin=50,
        bottomMargin=40,
    )

    styles = getSampleStyleSheet()
    elements = []

    # Título
    elements.append(Paragraph(
        f"Histórico de Recompensas — {customer_email}",
        styles["Heading1"],
    ))
    elements.append(Paragraph(
        f"Saldo atual: {rewards.total_points} pts &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"Nível: {rewards.tier} &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"Total acumulado: {rewards.lifetime_points_earned} pts",
        styles["Normal"],
    ))

    # Espaçamento
    elements.append(Paragraph("<br/>", styles["Normal"]))

    # Tabela
    table_data = [_CSV_HEADERS]
    for tx in transactions:
        table_data.append([
            str(tx.id),
            tx.transaction_type,
            str(tx.points),
            tx.reason,
            str(tx.rental_id) if tx.rental_id is not None else "",
            tx.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        ])

    col_widths = [35, 65, 50, 200, 60, 120]
    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c5f8a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (3, 1), (3, -1), "LEFT"),       # reason — alinhado à esquerda
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eef3f8")]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bbbbbb")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(table)

    doc.build(elements)

    pdf_bytes = buffer.getvalue()
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="rewards_{customer_email}.pdf"'
    )
    return response


@api_view(["GET"])
def export_rewards_history(request, customer_email):
    """
    Exportar o histórico de transações do cliente como CSV ou PDF.

    GET /api/rewards/customer/{customer_email}/history/export/

    Query params:
      export_format — formato de saída: 'csv' (padrão) ou 'pdf'
      type          — filtrar por tipo: 'earned' ou 'redeemed'
      ordering      — ordenar por: 'timestamp' (asc) ou '-timestamp' (desc, padrão)

    Nota: usa 'export_format' em vez de 'format' pois 'format' é reservado
    pelo DRF para negociação de conteúdo.

    Retorna um arquivo para download com todos os registros correspondentes
    ao filtro (sem paginação — o export é sempre completo).
    """
    rewards = database.get_customer_rewards(customer_email)
    if rewards is None:
        return Response(
            {"error": "Nenhum registro de recompensas encontrado para este cliente."},
            status=status.HTTP_404_NOT_FOUND,
        )

    export_format = request.query_params.get("export_format", "csv")
    if export_format not in _VALID_FORMATS:
        return Response(
            {"error": f"Formato inválido. Use: {', '.join(sorted(_VALID_FORMATS))}."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    transaction_type = request.query_params.get("type")
    if transaction_type and transaction_type not in _VALID_TYPES:
        return Response(
            {"error": f"Tipo inválido. Use: {', '.join(sorted(_VALID_TYPES))}."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    ordering_param = request.query_params.get("ordering", "-timestamp")
    ordering = _ORDERING_MAP.get(ordering_param, "-created_at")

    transactions = list(database.get_reward_transactions(customer_email, transaction_type, ordering))

    if export_format == "pdf":
        return _build_pdf_response(customer_email, transactions, rewards)

    return _build_csv_response(customer_email, transactions)


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
