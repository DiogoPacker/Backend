"""
Testes do app 'rewards'.

Cobre:
  RewardCalculationTests      — lógica pura: get_car_category, get_customer_tier,
                                 get_points_to_next_tier, calculate_rental_points
  RewardDatabaseTests         — camada rewards.database (ORM, atomicidade)
  RewardAPITestCase           — endpoints GET /api/rewards/customer/<email>/
                                 GET /api/rewards/customer/<email>/history/
                                 POST /api/rewards/apply/
  RewardHistoryFiltersTestCase — paginação, filtragem, ordenação e exportação CSV
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from rentals.models import Car, Rental
from rewards import database as rewards_db
from rewards.models import CustomerRewards, RewardTransaction
from rewards.utils import (
    BASE_POINTS_PER_DAY,
    CATEGORY_BONUS_PER_DAY,
    DURATION_BONUS,
    ON_TIME_RETURN_BONUS,
    TIER_MULTIPLIERS,
    TIER_THRESHOLDS,
    calculate_rental_points,
    get_car_category,
    get_customer_tier,
    get_points_to_next_tier,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_car(daily_rate="200.00", available=True, model="TestCar"):
    return Car.objects.create(
        brand="Brand", model=model, year=2023,
        daily_rate=Decimal(daily_rate), available=available,
    )


def _make_rental(car, days=5, on_time=True, extra_late_days=0,
                 email="cliente@test.com", returned=True):
    """
    Cria Rental no banco com datas controladas.
    Quando on_time=False, extra_late_days define o atraso (mínimo 1 dia).
    """
    start = timezone.now() - timedelta(days=days + 2)
    end = start + timedelta(days=days)
    actual = end if on_time else end + timedelta(days=max(extra_late_days, 1))
    return Rental.objects.create(
        car=car,
        customer_name="Cliente",
        customer_email=email,
        start_date=start,
        end_date=end,
        actual_return_date=actual,
        total_cost=Decimal(str(float(car.daily_rate) * days)),
        returned=returned,
    )


def _make_rewards(email="cliente@test.com", points=0, tier="Bronze"):
    return CustomerRewards.objects.create(
        customer_email=email,
        total_points=points,
        tier=tier,
        lifetime_points_earned=points,
        lifetime_points_redeemed=0,
    )


# ---------------------------------------------------------------------------
# 1. RewardCalculationTests  (lógica pura, sem HTTP)
# ---------------------------------------------------------------------------

class RewardCalculationTests(TestCase):
    """
    Testa rewards/utils.py de forma isolada.
    Nenhuma chamada HTTP — instâncias criadas diretamente no banco de teste.
    """

    def setUp(self):
        self.eco = _make_car(daily_rate="150.00", model="Eco")
        self.std = _make_car(daily_rate="350.00", model="Std")
        self.pre = _make_car(daily_rate="600.00", model="Pre")

    # --- get_car_category ---

    def test_category_economy_below_300(self):
        self.assertEqual(get_car_category(Decimal("100")), "economy")

    def test_category_economy_at_299(self):
        self.assertEqual(get_car_category(Decimal("299.99")), "economy")

    def test_category_standard_at_300(self):
        self.assertEqual(get_car_category(Decimal("300")), "standard")

    def test_category_standard_at_499(self):
        self.assertEqual(get_car_category(Decimal("499.99")), "standard")

    def test_category_premium_at_500(self):
        self.assertEqual(get_car_category(Decimal("500")), "premium")

    def test_category_premium_above_500(self):
        self.assertEqual(get_car_category(Decimal("2000")), "premium")

    def test_category_accepts_string_input(self):
        """get_car_category converte internamente para Decimal."""
        self.assertEqual(get_car_category("600"), "premium")

    # --- get_customer_tier ---

    def test_tier_bronze_at_0(self):
        self.assertEqual(get_customer_tier(0), "Bronze")

    def test_tier_bronze_at_499(self):
        self.assertEqual(get_customer_tier(499), "Bronze")

    def test_tier_silver_at_500(self):
        self.assertEqual(get_customer_tier(500), "Silver")

    def test_tier_silver_at_999(self):
        self.assertEqual(get_customer_tier(999), "Silver")

    def test_tier_gold_at_1000(self):
        self.assertEqual(get_customer_tier(1000), "Gold")

    def test_tier_gold_above_1000(self):
        self.assertEqual(get_customer_tier(9999), "Gold")

    # --- get_points_to_next_tier ---

    def test_points_to_next_at_0_is_500(self):
        self.assertEqual(get_points_to_next_tier(0), 500)

    def test_points_to_next_at_200_is_300(self):
        self.assertEqual(get_points_to_next_tier(200), 300)

    def test_points_to_next_at_499_is_1(self):
        self.assertEqual(get_points_to_next_tier(499), 1)

    def test_points_to_next_at_500_is_500(self):
        self.assertEqual(get_points_to_next_tier(500), 500)

    def test_points_to_next_at_750_is_250(self):
        self.assertEqual(get_points_to_next_tier(750), 250)

    def test_points_to_next_at_gold_is_none(self):
        self.assertIsNone(get_points_to_next_tier(1000))

    def test_points_to_next_above_gold_is_none(self):
        self.assertIsNone(get_points_to_next_tier(5000))

    # --- calculate_rental_points: cenário exato do FEATURE.md ---

    def test_cenario_feature_md_bronze(self):
        """
        FEATURE.md: Audi Q3 (R$600/dia), 8 dias, pontual, Bronze.
        Cálculo: (10+10)×8 + 50 + 25 = 235 pts.
        """
        rental = _make_rental(self.pre, days=8, on_time=True)
        self.assertEqual(calculate_rental_points(rental, "Bronze"), 235)

    def test_cenario_feature_md_silver(self):
        """
        FEATURE.md: mesmo cenário com Silver.
        235 × 1.25 = 293.75 → 294 pts (ROUND_HALF_UP).
        """
        rental = _make_rental(self.pre, days=8, on_time=True)
        self.assertEqual(calculate_rental_points(rental, "Silver"), 294)

    def test_cenario_feature_md_gold(self):
        """
        235 × 1.5 = 352.5 → 353 pts (ROUND_HALF_UP).
        """
        rental = _make_rental(self.pre, days=8, on_time=True)
        self.assertEqual(calculate_rental_points(rental, "Gold"), 353)

    # --- regras individuais ---

    def test_base_points_economy_3_days_late(self):
        """Economy, 3 dias, atrasado: apenas pontos base."""
        rental = _make_rental(self.eco, days=3, on_time=False)
        # 10 × 3 = 30
        self.assertEqual(calculate_rental_points(rental, "Bronze"), 30)

    def test_standard_category_bonus_per_day(self):
        rental = _make_rental(self.std, days=3, on_time=False)
        # (10+5) × 3 = 45
        self.assertEqual(calculate_rental_points(rental, "Bronze"), 45)

    def test_premium_category_bonus_per_day(self):
        rental = _make_rental(self.pre, days=3, on_time=False)
        # (10+10) × 3 = 60
        self.assertEqual(calculate_rental_points(rental, "Bronze"), 60)

    def test_duration_bonus_exactly_7_days(self):
        rental = _make_rental(self.eco, days=7, on_time=False)
        # 10×7 + 50 = 120
        self.assertEqual(calculate_rental_points(rental, "Bronze"), 120)

    def test_duration_bonus_exactly_14_days_uses_highest_only(self):
        """14 dias recebe +150, não +50+150 (apenas o maior nível aplicável)."""
        rental = _make_rental(self.eco, days=14, on_time=False)
        # 10×14 + 150 = 290
        self.assertEqual(calculate_rental_points(rental, "Bronze"), 290)

    def test_duration_bonus_6_days_no_bonus(self):
        rental = _make_rental(self.eco, days=6, on_time=False)
        # 10×6 = 60
        self.assertEqual(calculate_rental_points(rental, "Bronze"), 60)

    def test_on_time_bonus_present_when_punctual(self):
        on_time = _make_rental(self.eco, days=3, on_time=True)
        late = _make_rental(self.eco, days=3, on_time=False, extra_late_days=1)
        diff = calculate_rental_points(on_time, "Bronze") - calculate_rental_points(late, "Bronze")
        self.assertEqual(diff, ON_TIME_RETURN_BONUS)

    def test_late_return_gives_no_on_time_bonus(self):
        late = _make_rental(self.eco, days=3, on_time=False, extra_late_days=5)
        # sem bônus pontualidade, mas pontos base preservados
        self.assertEqual(calculate_rental_points(late, "Bronze"), 30)

    def test_late_return_does_not_penalize_existing_points(self):
        late = _make_rental(self.eco, days=3, on_time=False, extra_late_days=10)
        self.assertGreater(calculate_rental_points(late, "Bronze"), 0)

    def test_silver_multiplier_125x(self):
        rental = _make_rental(self.eco, days=4, on_time=False)
        # (10×4) = 40 base; Silver: 40 × 1.25 = 50
        self.assertEqual(calculate_rental_points(rental, "Silver"), 50)

    def test_gold_multiplier_15x(self):
        rental = _make_rental(self.eco, days=4, on_time=False)
        # 40 × 1.5 = 60
        self.assertEqual(calculate_rental_points(rental, "Gold"), 60)

    def test_minimum_one_day_when_start_equals_end(self):
        """Locação com start=end não deve gerar 0 dias."""
        now = timezone.now() - timedelta(hours=1)
        rental = Rental.objects.create(
            car=self.eco, customer_name="T", customer_email="t@t.com",
            start_date=now, end_date=now, actual_return_date=now,
            total_cost=Decimal("100"), returned=True,
        )
        self.assertGreaterEqual(calculate_rental_points(rental, "Bronze"), BASE_POINTS_PER_DAY)


# ---------------------------------------------------------------------------
# 2. RewardDatabaseTests
# ---------------------------------------------------------------------------

class RewardDatabaseTests(TestCase):
    """Testa rewards/database.py: persistência, atomicidade e regras de negócio."""

    def setUp(self):
        self.car = _make_car()

    def test_get_or_create_creates_on_first_call(self):
        rewards = rewards_db.get_or_create_customer_rewards("novo@test.com")
        self.assertEqual(rewards.customer_email, "novo@test.com")
        self.assertEqual(rewards.total_points, 0)
        self.assertEqual(rewards.tier, "Bronze")

    def test_get_or_create_is_idempotent(self):
        r1 = rewards_db.get_or_create_customer_rewards("idem@test.com")
        r2 = rewards_db.get_or_create_customer_rewards("idem@test.com")
        self.assertEqual(r1.id, r2.id)
        self.assertEqual(CustomerRewards.objects.filter(customer_email="idem@test.com").count(), 1)

    def test_get_customer_rewards_returns_none_when_absent(self):
        self.assertIsNone(rewards_db.get_customer_rewards("fantasma@test.com"))

    def test_get_customer_rewards_returns_object_when_present(self):
        _make_rewards(email="present@test.com", points=100)
        result = rewards_db.get_customer_rewards("present@test.com")
        self.assertIsNotNone(result)
        self.assertEqual(result.total_points, 100)

    def test_add_earned_points_increments_balance(self):
        rewards = rewards_db.get_or_create_customer_rewards("earn@test.com")
        rental = _make_rental(self.car, days=3)
        rewards_db.add_earned_points(rewards, rental, 100, "Teste")
        rewards.refresh_from_db()
        self.assertEqual(rewards.total_points, 100)
        self.assertEqual(rewards.lifetime_points_earned, 100)

    def test_add_earned_points_creates_transaction_record(self):
        rewards = rewards_db.get_or_create_customer_rewards("earn2@test.com")
        rental = _make_rental(self.car, days=3)
        rewards_db.add_earned_points(rewards, rental, 50, "Motivo")
        tx = RewardTransaction.objects.get(customer_rewards=rewards)
        self.assertEqual(tx.transaction_type, "earned")
        self.assertEqual(tx.points, 50)
        self.assertEqual(tx.reason, "Motivo")

    def test_add_earned_points_updates_tier_on_threshold(self):
        rewards = rewards_db.get_or_create_customer_rewards("tier@test.com")
        rental = _make_rental(self.car, days=3)
        rewards_db.add_earned_points(rewards, rental, 500, "Tier up")
        rewards.refresh_from_db()
        self.assertEqual(rewards.tier, "Silver")

    def test_add_earned_points_updates_tier_to_gold(self):
        rewards = _make_rewards(email="gold@test.com", points=900, tier="Silver")
        rental = _make_rental(self.car, days=3)
        rewards_db.add_earned_points(rewards, rental, 200, "Gold up")
        rewards.refresh_from_db()
        self.assertEqual(rewards.tier, "Gold")

    def test_redeem_points_success(self):
        rewards = _make_rewards(email="redeem@test.com", points=200)
        rental = _make_rental(self.car, days=3)
        tx, error = rewards_db.redeem_points(rewards, rental, 100)
        self.assertEqual(error, "")
        self.assertIsNotNone(tx)
        rewards.refresh_from_db()
        self.assertEqual(rewards.total_points, 100)
        self.assertEqual(rewards.lifetime_points_redeemed, 100)

    def test_redeem_points_creates_negative_transaction(self):
        rewards = _make_rewards(email="redeem2@test.com", points=200)
        rental = _make_rental(self.car, days=3)
        rewards_db.redeem_points(rewards, rental, 100)
        tx = RewardTransaction.objects.filter(
            customer_rewards=rewards, transaction_type="redeemed"
        ).first()
        self.assertIsNotNone(tx)
        self.assertEqual(tx.points, -100)

    def test_redeem_points_insufficient_balance_returns_error(self):
        rewards = _make_rewards(email="broke@test.com", points=50)
        rental = _make_rental(self.car, days=3)
        tx, error = rewards_db.redeem_points(rewards, rental, 100)
        self.assertIsNone(tx)
        self.assertIn("Saldo insuficiente", error)
        rewards.refresh_from_db()
        self.assertEqual(rewards.total_points, 50)  # não alterado

    def test_redeem_points_below_minimum_returns_error(self):
        rewards = _make_rewards(email="min@test.com", points=200)
        rental = _make_rental(self.car, days=3)
        tx, error = rewards_db.redeem_points(rewards, rental, 50)
        self.assertIsNone(tx)
        self.assertGreater(len(error), 0)

    def test_get_reward_transactions_returns_customer_history(self):
        rewards = _make_rewards(email="hist@test.com", points=0)
        rental = _make_rental(self.car, days=3)
        rewards_db.add_earned_points(rewards, rental, 100, "T1")
        rewards_db.add_earned_points(rewards, rental, 50, "T2")
        txs = list(rewards_db.get_reward_transactions("hist@test.com"))
        self.assertEqual(len(txs), 2)

    def test_get_reward_transactions_does_not_return_other_customers(self):
        r1 = _make_rewards(email="c1@test.com", points=0)
        r2 = _make_rewards(email="c2@test.com", points=0)
        rental = _make_rental(self.car, days=3)
        rewards_db.add_earned_points(r1, rental, 100, "C1")
        txs = list(rewards_db.get_reward_transactions("c2@test.com"))
        self.assertEqual(len(txs), 0)


# ---------------------------------------------------------------------------
# 3. RewardAPITestCase
# ---------------------------------------------------------------------------

class RewardAPITestCase(TestCase):
    """Testa os endpoints HTTP do app rewards."""

    def setUp(self):
        self.client = APIClient()
        # Criar carro premium e fazer uma locação + devolução via API
        # para garantir que CustomerRewards exista para os testes
        self.car = _make_car(daily_rate="600.00", model="Premium")
        resp = self.client.post(
            "/api/rentals/create/",
            {"car_id": self.car.id, "customer_name": "Maria",
             "customer_email": "maria@test.com", "days": 8},
            format="json",
        )
        self.rental_id = resp.data["id"]
        self.client.post(f"/api/rentals/{self.rental_id}/return/")

    # --- GET /api/rewards/customer/<email>/ ---

    def test_get_rewards_returns_200(self):
        resp = self.client.get("/api/rewards/customer/maria@test.com/")
        self.assertEqual(resp.status_code, 200)

    def test_get_rewards_fields_present(self):
        resp = self.client.get("/api/rewards/customer/maria@test.com/")
        for field in ("customer_email", "total_points", "tier",
                      "points_to_next_tier", "lifetime_points_earned",
                      "lifetime_points_redeemed"):
            self.assertIn(field, resp.data)

    def test_get_rewards_correct_email(self):
        resp = self.client.get("/api/rewards/customer/maria@test.com/")
        self.assertEqual(resp.data["customer_email"], "maria@test.com")

    def test_get_rewards_cenario_feature_md_235_points(self):
        """Premium 8 dias pontual Bronze → 235 pts (cenário exato do FEATURE.md)."""
        resp = self.client.get("/api/rewards/customer/maria@test.com/")
        self.assertEqual(resp.data["total_points"], 235)

    def test_get_rewards_tier_is_bronze_for_new_customer(self):
        resp = self.client.get("/api/rewards/customer/maria@test.com/")
        self.assertEqual(resp.data["tier"], "Bronze")

    def test_get_rewards_points_to_next_tier_correct(self):
        resp = self.client.get("/api/rewards/customer/maria@test.com/")
        # Bronze: 500 - 235 = 265
        self.assertEqual(resp.data["points_to_next_tier"], 265)

    def test_get_rewards_nonexistent_returns_404(self):
        resp = self.client.get("/api/rewards/customer/naoexiste@test.com/")
        self.assertEqual(resp.status_code, 404)

    def test_get_rewards_lifetime_earned_equals_total_no_redemptions(self):
        resp = self.client.get("/api/rewards/customer/maria@test.com/")
        self.assertEqual(resp.data["lifetime_points_earned"], resp.data["total_points"])
        self.assertEqual(resp.data["lifetime_points_redeemed"], 0)

    # --- GET /api/rewards/customer/<email>/history/ ---

    def test_get_history_returns_200(self):
        resp = self.client.get("/api/rewards/customer/maria@test.com/history/")
        self.assertEqual(resp.status_code, 200)

    def test_get_history_has_transactions_key(self):
        resp = self.client.get("/api/rewards/customer/maria@test.com/history/")
        self.assertIn("transactions", resp.data)
        self.assertIn("customer_email", resp.data)

    def test_get_history_has_one_transaction_after_return(self):
        resp = self.client.get("/api/rewards/customer/maria@test.com/history/")
        self.assertEqual(len(resp.data["transactions"]), 1)

    def test_get_history_transaction_fields(self):
        resp = self.client.get("/api/rewards/customer/maria@test.com/history/")
        tx = resp.data["transactions"][0]
        for field in ("id", "type", "points", "reason", "rental_id", "timestamp"):
            self.assertIn(field, tx)

    def test_get_history_transaction_type_is_earned(self):
        resp = self.client.get("/api/rewards/customer/maria@test.com/history/")
        tx = resp.data["transactions"][0]
        self.assertEqual(tx["type"], "earned")

    def test_get_history_transaction_points_match_balance(self):
        resp_hist = self.client.get("/api/rewards/customer/maria@test.com/history/")
        resp_bal = self.client.get("/api/rewards/customer/maria@test.com/")
        self.assertEqual(resp_hist.data["transactions"][0]["points"],
                         resp_bal.data["total_points"])

    def test_get_history_nonexistent_returns_404(self):
        resp = self.client.get("/api/rewards/customer/fantasma@test.com/history/")
        self.assertEqual(resp.status_code, 404)

    def test_get_history_grows_after_second_rental(self):
        car2 = _make_car(daily_rate="200.00", model="Eco2")
        c = self.client.post(
            "/api/rentals/create/",
            {"car_id": car2.id, "customer_name": "Maria",
             "customer_email": "maria@test.com", "days": 2},
            format="json",
        )
        self.client.post(f"/api/rentals/{c.data['id']}/return/")
        resp = self.client.get("/api/rewards/customer/maria@test.com/history/")
        self.assertEqual(len(resp.data["transactions"]), 2)

    # --- POST /api/rewards/apply/ ---

    def test_apply_rewards_success_returns_200(self):
        payload = {"rental_id": self.rental_id,
                   "customer_email": "maria@test.com", "points_to_redeem": 100}
        resp = self.client.post("/api/rewards/apply/", payload, format="json")
        self.assertEqual(resp.status_code, 200)

    def test_apply_rewards_discount_100_points_equals_50_brl(self):
        payload = {"rental_id": self.rental_id,
                   "customer_email": "maria@test.com", "points_to_redeem": 100}
        resp = self.client.post("/api/rewards/apply/", payload, format="json")
        self.assertEqual(resp.data["discount_brl"], 50)

    def test_apply_rewards_200_points_equals_100_brl(self):
        payload = {"rental_id": self.rental_id,
                   "customer_email": "maria@test.com", "points_to_redeem": 200}
        resp = self.client.post("/api/rewards/apply/", payload, format="json")
        self.assertEqual(resp.data["discount_brl"], 100)

    def test_apply_rewards_deducts_points_from_balance(self):
        self.client.post(
            "/api/rewards/apply/",
            {"rental_id": self.rental_id, "customer_email": "maria@test.com",
             "points_to_redeem": 100},
            format="json",
        )
        rewards = CustomerRewards.objects.get(customer_email="maria@test.com")
        self.assertEqual(rewards.total_points, 135)  # 235 - 100

    def test_apply_rewards_response_has_remaining_points(self):
        payload = {"rental_id": self.rental_id,
                   "customer_email": "maria@test.com", "points_to_redeem": 100}
        resp = self.client.post("/api/rewards/apply/", payload, format="json")
        self.assertIn("remaining_points", resp.data)
        self.assertEqual(resp.data["remaining_points"], 135)

    def test_apply_rewards_below_minimum_returns_400(self):
        payload = {"rental_id": self.rental_id,
                   "customer_email": "maria@test.com", "points_to_redeem": 50}
        resp = self.client.post("/api/rewards/apply/", payload, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_apply_rewards_insufficient_balance_returns_400(self):
        payload = {"rental_id": self.rental_id,
                   "customer_email": "maria@test.com", "points_to_redeem": 10000}
        resp = self.client.post("/api/rewards/apply/", payload, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_apply_rewards_wrong_owner_returns_403(self):
        payload = {"rental_id": self.rental_id,
                   "customer_email": "outro@test.com", "points_to_redeem": 100}
        resp = self.client.post("/api/rewards/apply/", payload, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_apply_rewards_nonexistent_rental_returns_404(self):
        payload = {"rental_id": 99999,
                   "customer_email": "maria@test.com", "points_to_redeem": 100}
        resp = self.client.post("/api/rewards/apply/", payload, format="json")
        self.assertEqual(resp.status_code, 404)

    def test_apply_rewards_nonexistent_customer_returns_404(self):
        payload = {"rental_id": self.rental_id,
                   "customer_email": "inexistente@test.com", "points_to_redeem": 100}
        resp = self.client.post("/api/rewards/apply/", payload, format="json")
        # inexistente@test.com não é dono da rental → 403 antes do 404
        self.assertIn(resp.status_code, (403, 404))

    # --- integração: tier upgrade via API ---

    def test_tier_upgrades_to_silver_after_enough_points(self):
        """Acumular 500+ pontos via múltiplas devoluções deve promover para Silver."""
        # Cada devolução de premium 8 dias = 235 pts; 3 devoluções = 705 pts → Silver
        for _ in range(2):  # já tem 235 do setUp
            car = _make_car(daily_rate="600.00", model=f"Extra{_}")
            c = self.client.post(
                "/api/rentals/create/",
                {"car_id": car.id, "customer_name": "Maria",
                 "customer_email": "maria@test.com", "days": 8},
                format="json",
            )
            self.client.post(f"/api/rentals/{c.data['id']}/return/")
        resp = self.client.get("/api/rewards/customer/maria@test.com/")
        self.assertIn(resp.data["tier"], ("Silver", "Gold"))
        self.assertGreaterEqual(resp.data["total_points"], 500)


# ---------------------------------------------------------------------------
# 4. RewardHistoryFiltersTestCase  — paginação, filtragem, ordenação, CSV
# ---------------------------------------------------------------------------

class RewardHistoryFiltersTestCase(TestCase):
    """
    Testa os recursos opcionais do endpoint GET /history/:
      - Filtragem por tipo de transação (?type=earned|redeemed)
      - Ordenação por timestamp (?ordering=timestamp|-timestamp)
      - Paginação (?page=N&page_size=N)
      - Exportação CSV (/history/export/)
    """

    BASE_URL = "/api/rewards/customer/filtros@test.com/history/"
    EXPORT_URL = "/api/rewards/customer/filtros@test.com/history/export/"

    def setUp(self):
        self.client = APIClient()
        self.car = _make_car(daily_rate="600.00", model="Premium")
        self.rewards = _make_rewards(email="filtros@test.com", points=500, tier="Silver")

        # Cria 3 transações: 2 earned + 1 redeemed
        rental1 = _make_rental(self.car, days=3, email="filtros@test.com")
        rental2 = _make_rental(self.car, days=3, email="filtros@test.com")
        rewards_db.add_earned_points(self.rewards, rental1, 100, "Ganho 1")
        rewards_db.add_earned_points(self.rewards, rental2, 200, "Ganho 2")
        rewards_db.redeem_points(self.rewards, rental1, 100)

    # --- filtragem ---

    def test_filter_by_type_earned_returns_only_earned(self):
        resp = self.client.get(self.BASE_URL, {"type": "earned"})
        self.assertEqual(resp.status_code, 200)
        types = [tx["type"] for tx in resp.data["transactions"]]
        self.assertTrue(all(t == "earned" for t in types))
        self.assertEqual(len(types), 2)

    def test_filter_by_type_redeemed_returns_only_redeemed(self):
        resp = self.client.get(self.BASE_URL, {"type": "redeemed"})
        self.assertEqual(resp.status_code, 200)
        types = [tx["type"] for tx in resp.data["transactions"]]
        self.assertTrue(all(t == "redeemed" for t in types))
        self.assertEqual(len(types), 1)

    def test_filter_invalid_type_returns_400(self):
        resp = self.client.get(self.BASE_URL, {"type": "invalido"})
        self.assertEqual(resp.status_code, 400)

    def test_no_filter_returns_all_transactions(self):
        resp = self.client.get(self.BASE_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 3)

    # --- ordenação ---

    def test_default_ordering_is_newest_first(self):
        resp = self.client.get(self.BASE_URL)
        timestamps = [tx["timestamp"] for tx in resp.data["transactions"]]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))

    def test_ordering_timestamp_asc(self):
        resp = self.client.get(self.BASE_URL, {"ordering": "timestamp"})
        self.assertEqual(resp.status_code, 200)
        timestamps = [tx["timestamp"] for tx in resp.data["transactions"]]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_ordering_timestamp_desc(self):
        resp = self.client.get(self.BASE_URL, {"ordering": "-timestamp"})
        self.assertEqual(resp.status_code, 200)
        timestamps = [tx["timestamp"] for tx in resp.data["transactions"]]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))

    def test_invalid_ordering_falls_back_to_default(self):
        """Parâmetro de ordenação desconhecido deve usar o padrão (-created_at)."""
        resp = self.client.get(self.BASE_URL, {"ordering": "campo_invalido"})
        self.assertEqual(resp.status_code, 200)
        # Apenas verifica que retornou sem erro
        self.assertIn("transactions", resp.data)

    # --- paginação ---

    def test_pagination_metadata_keys_present(self):
        resp = self.client.get(self.BASE_URL)
        for key in ("count", "total_pages", "page", "page_size", "transactions"):
            self.assertIn(key, resp.data)

    def test_pagination_page_size_1_returns_one_item(self):
        resp = self.client.get(self.BASE_URL, {"page_size": 1})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["transactions"]), 1)
        self.assertEqual(resp.data["count"], 3)
        self.assertEqual(resp.data["total_pages"], 3)

    def test_pagination_page_2_returns_correct_item(self):
        resp = self.client.get(self.BASE_URL, {"page_size": 1, "page": 2})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["transactions"]), 1)
        self.assertEqual(resp.data["page"], 2)

    def test_pagination_out_of_range_returns_last_page(self):
        resp = self.client.get(self.BASE_URL, {"page": 9999, "page_size": 10})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("transactions", resp.data)

    def test_pagination_page_size_capped_at_100(self):
        resp = self.client.get(self.BASE_URL, {"page_size": 9999})
        self.assertEqual(resp.status_code, 200)
        self.assertLessEqual(resp.data["page_size"], 100)

    def test_pagination_invalid_page_size_uses_default(self):
        resp = self.client.get(self.BASE_URL, {"page_size": "abc"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["page_size"], 20)

    # --- exportação CSV ---

    def test_csv_export_returns_200(self):
        resp = self.client.get(self.EXPORT_URL)
        self.assertEqual(resp.status_code, 200)

    def test_csv_export_content_type_is_csv(self):
        resp = self.client.get(self.EXPORT_URL)
        self.assertIn("text/csv", resp["Content-Type"])

    def test_csv_export_has_content_disposition(self):
        resp = self.client.get(self.EXPORT_URL)
        self.assertIn("attachment", resp["Content-Disposition"])
        self.assertIn("filtros@test.com", resp["Content-Disposition"])

    def test_csv_export_has_header_row(self):
        resp = self.client.get(self.EXPORT_URL)
        lines = resp.content.decode("utf-8").strip().splitlines()
        self.assertEqual(lines[0], "id,type,points,reason,rental_id,timestamp")

    def test_csv_export_row_count_matches_transactions(self):
        resp = self.client.get(self.EXPORT_URL)
        lines = resp.content.decode("utf-8").strip().splitlines()
        # 1 cabeçalho + 3 transações
        self.assertEqual(len(lines), 4)

    def test_csv_export_filter_by_type(self):
        resp = self.client.get(self.EXPORT_URL, {"type": "earned"})
        self.assertEqual(resp.status_code, 200)
        lines = resp.content.decode("utf-8").strip().splitlines()
        # 1 cabeçalho + 2 earned
        self.assertEqual(len(lines), 3)

    def test_csv_export_nonexistent_customer_returns_404(self):
        resp = self.client.get(
            "/api/rewards/customer/naoexiste@test.com/history/export/"
        )
        self.assertEqual(resp.status_code, 404)

    # --- exportação PDF ---

    def test_pdf_export_returns_200(self):
        resp = self.client.get(self.EXPORT_URL, {"export_format": "pdf"})
        self.assertEqual(resp.status_code, 200)

    def test_pdf_export_content_type_is_pdf(self):
        resp = self.client.get(self.EXPORT_URL, {"export_format": "pdf"})
        self.assertEqual(resp["Content-Type"], "application/pdf")

    def test_pdf_export_has_content_disposition(self):
        resp = self.client.get(self.EXPORT_URL, {"export_format": "pdf"})
        self.assertIn("attachment", resp["Content-Disposition"])
        self.assertIn("filtros@test.com", resp["Content-Disposition"])
        self.assertIn(".pdf", resp["Content-Disposition"])

    def test_pdf_export_body_is_non_empty(self):
        resp = self.client.get(self.EXPORT_URL, {"export_format": "pdf"})
        self.assertGreater(len(resp.content), 100)  # PDF não pode ser vazio

    def test_pdf_export_starts_with_pdf_magic_bytes(self):
        """Arquivo gerado deve ser um PDF válido (magic bytes %PDF)."""
        resp = self.client.get(self.EXPORT_URL, {"export_format": "pdf"})
        self.assertTrue(resp.content.startswith(b"%PDF"))

    def test_pdf_export_filter_by_type_earned(self):
        """PDF filtrado por 'earned' deve ser gerado sem erro."""
        resp = self.client.get(self.EXPORT_URL, {"export_format": "pdf", "type": "earned"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")

    def test_pdf_export_filter_by_type_redeemed(self):
        resp = self.client.get(self.EXPORT_URL, {"export_format": "pdf", "type": "redeemed"})
        self.assertEqual(resp.status_code, 200)

    def test_pdf_export_invalid_type_returns_400(self):
        resp = self.client.get(self.EXPORT_URL, {"export_format": "pdf", "type": "invalido"})
        self.assertEqual(resp.status_code, 400)

    def test_pdf_export_nonexistent_customer_returns_404(self):
        resp = self.client.get(
            "/api/rewards/customer/naoexiste@test.com/history/export/",
            {"export_format": "pdf"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_invalid_export_format_returns_400(self):
        resp = self.client.get(self.EXPORT_URL, {"export_format": "xlsx"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.data)

    def test_default_export_format_is_csv(self):
        """Sem ?export_format= deve retornar CSV (retrocompatibilidade)."""
        resp = self.client.get(self.EXPORT_URL)
        self.assertIn("text/csv", resp["Content-Type"])

