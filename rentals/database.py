# Database access layer
from django.db.models import Count, Sum, Q
from .models import Car, Rental


def get_all_cars():
    cars = Car.objects.all()
    return cars


def get_available_cars():
    """Obter todos os carros disponíveis.

    REFACTOR: a versão original iterava todos os carros em Python (O(n) em memória).
    Substituído por filter() que delega o filtro ao banco, reduzindo tráfego e memória.
    """
    return Car.objects.filter(available=True)


def get_car_by_id(car_id):
    try:
        car = Car.objects.get(id=car_id)
        return car
    except Car.DoesNotExist:
        return None


def create_rental(car_id, customer_name, customer_email, start_date, end_date, total_cost):
    rental = Rental.objects.create(
        car_id=car_id,
        customer_name=customer_name,
        customer_email=customer_email,
        start_date=start_date,
        end_date=end_date,
        total_cost=total_cost,
        returned=False
    )
    return rental


def get_rental_by_id(rental_id):
    try:
        return Rental.objects.select_related('car').get(id=rental_id)
    except Rental.DoesNotExist:
        return None


def get_all_rentals():
    """Retornar todas as locações com o carro pré-carregado."""
    return Rental.objects.select_related('car').all()


def get_customer_rentals(customer_email):
    """Obter locações de um cliente pelo e-mail.

    REFACTOR: a versão original carregava todas as locações e filtrava em Python.
    Substituído por filter() direto no ORM.
    """
    return Rental.objects.select_related('car').filter(customer_email=customer_email)


def update_rental(rental):
    rental.save()
    return rental


def update_car(car):
    car.save()
    return car


def get_rental_stats():
    """Calcular estatísticas de locações.

    REFACTOR: a versão original contava e somava tudo em Python com três loops.
    Substituído por aggregate() + Count() — o banco faz o trabalho em uma query.
    """
    rental_stats = Rental.objects.aggregate(
        total_rentals=Count('id'),
        active_rentals=Count('id', filter=Q(returned=False)),
        total_revenue=Sum('total_cost'),
    )
    car_stats = Car.objects.aggregate(
        total_cars=Count('id'),
        available_cars=Count('id', filter=Q(available=True)),
    )
    return {
        'total_rentals': rental_stats['total_rentals'] or 0,
        'active_rentals': rental_stats['active_rentals'] or 0,
        'available_cars': car_stats['available_cars'] or 0,
        'total_cars': car_stats['total_cars'] or 0,
        'total_revenue': float(rental_stats['total_revenue'] or 0),
    }

