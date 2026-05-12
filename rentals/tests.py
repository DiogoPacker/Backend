"""
Testes do app 'rentals'.

Cobre:
  CarAPITestCase        — endpoints GET /api/cars/ e /api/cars/<id>/
  RentalAPITestCase     — criação, devolução, multa por atraso e consultas
  DatabaseLayerTests    — camada rentals.database (ORM queries)
  UtilsTests            — rentals.utils (validate_rental_dates, descontos)

Os testes de cálculo de pontos e endpoints /api/rewards/ estão em rewards/tests.py.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from rentals import database as rental_database
from rentals.models import Car, Rental
from rentals.utils import calculate_discount, calculate_late_fee, validate_rental_dates


# ---------------------------------------------------------------------------
# Helpers compartilhados
# ---------------------------------------------------------------------------

def _make_car(daily_rate="200.00", available=True, brand="Maker", model="TestCar"):
    return Car.objects.create(
        brand=brand, model=model, year=2023,
        daily_rate=Decimal(daily_rate), available=available,
    )


def _create_rental_via_api(client, car_id, email="user@test.com", days=3):
    return client.post(
        "/api/rentals/create/",
        {"car_id": car_id, "customer_name": "Teste", "customer_email": email, "days": days},
        format="json",
    )


# ---------------------------------------------------------------------------
# 1. CarAPITestCase
# ---------------------------------------------------------------------------

class CarAPITestCase(TestCase):
    """Testa os endpoints de carros."""

    def setUp(self):
        self.client = APIClient()
        self.car = _make_car()

    def test_list_cars_returns_200(self):
        response = self.client.get("/api/cars/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("cars", response.data)

    def test_list_cars_only_available(self):
        _make_car(available=False, model="Bloqueado")
        response = self.client.get("/api/cars/")
        ids = [c["id"] for c in response.data["cars"]]
        self.assertIn(self.car.id, ids)
        self.assertEqual(len(ids), 1)

    def test_list_cars_fields_present(self):
        response = self.client.get("/api/cars/")
        car = response.data["cars"][0]
        for field in ("id", "brand", "model", "year", "daily_rate", "available"):
            self.assertIn(field, car)

    def test_get_car_by_id_returns_correct_car(self):
        response = self.client.get(f"/api/cars/{self.car.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["brand"], self.car.brand)
        self.assertEqual(response.data["model"], self.car.model)

    def test_get_car_not_found_returns_404(self):
        response = self.client.get("/api/cars/99999/")
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# 2. RentalAPITestCase
# ---------------------------------------------------------------------------

class RentalAPITestCase(TestCase):
    """Testa criação, devolução e consultas de locações."""

    def setUp(self):
        self.client = APIClient()
        self.car = _make_car(daily_rate="200.00")

    # --- criação ---

    def test_create_rental_returns_201(self):
        resp = _create_rental_via_api(self.client, self.car.id)
        self.assertEqual(resp.status_code, 201)

    def test_create_rental_fields_in_response(self):
        resp = _create_rental_via_api(self.client, self.car.id)
        for field in ("id", "car", "customer_name", "customer_email", "total_cost", "returned"):
            self.assertIn(field, resp.data)

    def test_create_rental_marks_car_unavailable(self):
        _create_rental_via_api(self.client, self.car.id)
        self.car.refresh_from_db()
        self.assertFalse(self.car.available)

    def test_create_rental_unavailable_car_returns_400(self):
        unavail = _make_car(available=False, model="Bloq")
        resp = _create_rental_via_api(self.client, unavail.id)
        self.assertEqual(resp.status_code, 400)

    def test_create_rental_nonexistent_car_returns_404(self):
        resp = _create_rental_via_api(self.client, 99999)
        self.assertEqual(resp.status_code, 404)

    def test_create_rental_applies_discount_for_long_rental(self):
        """Locações de 8 dias têm desconto de 10% sobre o custo bruto."""
        resp = _create_rental_via_api(self.client, self.car.id, days=8)
        self.assertEqual(resp.status_code, 201)
        # 200 × 8 = 1600 bruto; −10% = 1440
        self.assertEqual(Decimal(resp.data["total_cost"]), Decimal("1440.00"))

    def test_create_rental_applies_discount_4_to_7_days(self):
        """Locações de 5 dias têm desconto de 5%."""
        resp = _create_rental_via_api(self.client, self.car.id, days=5)
        # 200 × 5 = 1000; −5% = 950
        self.assertEqual(Decimal(resp.data["total_cost"]), Decimal("950.00"))

    def test_create_rental_no_discount_for_short_rental(self):
        resp = _create_rental_via_api(self.client, self.car.id, days=2)
        self.assertEqual(Decimal(resp.data["total_cost"]), Decimal("400.00"))

    # --- devolução ---

    def test_return_rental_returns_200(self):
        create = _create_rental_via_api(self.client, self.car.id)
        resp = self.client.post(f"/api/rentals/{create.data['id']}/return/")
        self.assertEqual(resp.status_code, 200)

    def test_return_rental_response_has_rewards_section(self):
        create = _create_rental_via_api(self.client, self.car.id)
        resp = self.client.post(f"/api/rentals/{create.data['id']}/return/")
        self.assertIn("rewards", resp.data)
        for key in ("points_earned", "total_points", "tier"):
            self.assertIn(key, resp.data["rewards"])

    def test_return_rental_credits_positive_points(self):
        create = _create_rental_via_api(self.client, self.car.id)
        resp = self.client.post(f"/api/rentals/{create.data['id']}/return/")
        self.assertGreater(resp.data["rewards"]["points_earned"], 0)

    def test_return_rental_marks_car_available(self):
        create = _create_rental_via_api(self.client, self.car.id)
        self.client.post(f"/api/rentals/{create.data['id']}/return/")
        self.car.refresh_from_db()
        self.assertTrue(self.car.available)

    def test_return_rental_already_returned_returns_400(self):
        create = _create_rental_via_api(self.client, self.car.id)
        rid = create.data["id"]
        self.client.post(f"/api/rentals/{rid}/return/")
        resp = self.client.post(f"/api/rentals/{rid}/return/")
        self.assertEqual(resp.status_code, 400)

    def test_return_nonexistent_rental_returns_404(self):
        resp = self.client.post("/api/rentals/99999/return/")
        self.assertEqual(resp.status_code, 404)

    def test_return_rental_marks_returned_true(self):
        create = _create_rental_via_api(self.client, self.car.id)
        rid = create.data["id"]
        self.client.post(f"/api/rentals/{rid}/return/")
        rental = Rental.objects.get(id=rid)
        self.assertTrue(rental.returned)
        self.assertIsNotNone(rental.actual_return_date)

    # --- consultas ---

    def test_get_all_rentals_returns_200(self):
        resp = self.client.get("/api/rentals/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("rentals", resp.data)

    def test_get_customer_rentals_filters_by_email(self):
        _create_rental_via_api(self.client, self.car.id, email="a@test.com")
        car2 = _make_car(model="Outro")
        _create_rental_via_api(self.client, car2.id, email="b@test.com")
        resp = self.client.get("/api/rentals/customer/a@test.com/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["rentals"]), 1)
        self.assertEqual(resp.data["rentals"][0]["customer_email"], "a@test.com")

    def test_get_stats_returns_expected_keys(self):
        resp = self.client.get("/api/stats/")
        self.assertEqual(resp.status_code, 200)
        for key in ("total_rentals", "active_rentals", "available_cars", "total_cars", "total_revenue"):
            self.assertIn(key, resp.data)

    def test_get_stats_counts_active_rentals(self):
        _create_rental_via_api(self.client, self.car.id)  # ativa
        stats = self.client.get("/api/stats/").data
        self.assertEqual(stats["active_rentals"], 1)
        self.assertEqual(stats["total_rentals"], 1)


# ---------------------------------------------------------------------------
# 3. DatabaseLayerTests
# ---------------------------------------------------------------------------

class DatabaseLayerTests(TestCase):
    """Testa as funções de rentals/database.py diretamente."""

    def setUp(self):
        self.car = _make_car()

    def _make_rental(self, returned=True, email="db@test.com", days=3):
        start = timezone.now() - timedelta(days=days + 1)
        return Rental.objects.create(
            car=self.car,
            customer_name="DB Teste",
            customer_email=email,
            start_date=start,
            end_date=start + timedelta(days=days),
            actual_return_date=start + timedelta(days=days),
            total_cost=Decimal(str(float(self.car.daily_rate) * days)),
            returned=returned,
        )

    def test_get_available_cars_only_available(self):
        _make_car(available=False, model="Bloq")
        cars = rental_database.get_available_cars()
        self.assertTrue(all(c.available for c in cars))
        self.assertEqual(len(list(cars)), 1)

    def test_get_available_cars_empty_when_none_available(self):
        self.car.available = False
        self.car.save()
        self.assertEqual(len(list(rental_database.get_available_cars())), 0)

    def test_get_rental_by_id_returns_correct_rental(self):
        r = self._make_rental()
        found = rental_database.get_rental_by_id(r.id)
        self.assertEqual(found.id, r.id)

    def test_get_rental_by_id_not_found_returns_none(self):
        self.assertIsNone(rental_database.get_rental_by_id(99999))

    def test_get_rental_by_id_has_car_prefetched(self):
        """select_related garante que rental.car não dispara query extra."""
        r = self._make_rental()
        found = rental_database.get_rental_by_id(r.id)
        # Acessar .car não deve levantar exceção nem disparar nova query
        self.assertIsNotNone(found.car)

    def test_get_customer_rentals_returns_only_matching(self):
        self._make_rental(email="x@test.com")
        car2 = _make_car(model="Outro")
        Rental.objects.create(
            car=car2, customer_name="Y", customer_email="y@test.com",
            start_date=timezone.now(), end_date=timezone.now() + timedelta(days=2),
            total_cost=Decimal("400"), returned=False,
        )
        results = list(rental_database.get_customer_rentals("x@test.com"))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].customer_email, "x@test.com")

    def test_get_rental_stats_total_and_active(self):
        self._make_rental(returned=True)
        car2 = _make_car(model="Ativo")
        Rental.objects.create(
            car=car2, customer_name="Ativo", customer_email="ativo@test.com",
            start_date=timezone.now(), end_date=timezone.now() + timedelta(days=3),
            total_cost=Decimal("600"), returned=False,
        )
        stats = rental_database.get_rental_stats()
        self.assertEqual(stats["total_rentals"], 2)
        self.assertEqual(stats["active_rentals"], 1)

    def test_get_rental_stats_available_cars(self):
        _make_car(available=False, model="Bloq")
        stats = rental_database.get_rental_stats()
        # self.car está disponível, o Bloq não
        self.assertEqual(stats["available_cars"], 1)
        self.assertEqual(stats["total_cars"], 2)

    def test_get_rental_stats_revenue(self):
        self._make_rental(days=3)  # 200 × 3 = 600
        stats = rental_database.get_rental_stats()
        self.assertAlmostEqual(stats["total_revenue"], 600.0, places=2)


# ---------------------------------------------------------------------------
# 4. UtilsTests
# ---------------------------------------------------------------------------

class UtilsTests(TestCase):
    """Testa rentals/utils.py: validate_rental_dates, calculate_discount, calculate_late_fee."""

    # --- validate_rental_dates ---

    def test_valid_dates_returns_empty_list(self):
        start = datetime(2025, 6, 1)
        end = datetime(2025, 6, 10)
        self.assertEqual(validate_rental_dates(start, end), [])

    def test_end_before_start_returns_error(self):
        errors = validate_rental_dates(datetime(2025, 6, 10), datetime(2025, 6, 1))
        self.assertGreater(len(errors), 0)

    def test_equal_dates_returns_error(self):
        d = datetime(2025, 6, 1)
        errors = validate_rental_dates(d, d)
        self.assertGreater(len(errors), 0)

    def test_none_start_returns_error(self):
        errors = validate_rental_dates(None, datetime(2025, 6, 1))
        self.assertGreater(len(errors), 0)

    def test_none_end_returns_error(self):
        errors = validate_rental_dates(datetime(2025, 6, 1), None)
        self.assertGreater(len(errors), 0)

    def test_both_none_returns_error(self):
        errors = validate_rental_dates(None, None)
        self.assertGreater(len(errors), 0)

    # --- calculate_discount ---

    def test_discount_no_days_le_3(self):
        self.assertEqual(calculate_discount(3, 100.0), 0)

    def test_discount_4_to_7_days_is_5_percent(self):
        self.assertAlmostEqual(calculate_discount(5, 1000.0), 50.0)

    def test_discount_over_7_days_is_10_percent(self):
        self.assertAlmostEqual(calculate_discount(8, 1000.0), 100.0)

    # --- calculate_late_fee ---

    def test_late_fee_correct_formula(self):
        # 2 dias × R$100/dia × 1.5 = R$300
        self.assertAlmostEqual(calculate_late_fee(2, 100.0), 300.0)
