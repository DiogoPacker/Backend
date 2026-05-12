from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal

from .models import Car, Rental
from .serializers import CarSerializer, RentalSerializer, RentalCreateSerializer
from . import database
from rewards import database as rewards_db
from rewards.utils import calculate_rental_points


@api_view(['GET'])
def index(request):
    """Endpoint de boas-vindas"""
    return Response({"message": "Welcome to Car Rental API"})


@api_view(['GET'])
def get_cars(request):
    cars = database.get_available_cars()
    serializer = CarSerializer(cars, many=True)
    return Response({"cars": serializer.data})


@api_view(['GET'])
def get_car(request, car_id):
    car = database.get_car_by_id(car_id)
    if car is None:
        return Response({"error": "Car not found"}, status=status.HTTP_404_NOT_FOUND)
    
    serializer = CarSerializer(car)
    return Response(serializer.data)


@api_view(['POST'])
def create_rental(request):
    """
    Criar uma nova locação
    """
    serializer = RentalCreateSerializer(data=request.data)
    
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    data = serializer.validated_data
    car_id = data['car_id']
    days = data['days']
    
    # Encontrar carro
    car = database.get_car_by_id(car_id)
    if car is None:
        return Response({"error": "Car not found"}, status=status.HTTP_404_NOT_FOUND)
    
    if car.available == False:
        return Response({"error": "Car is not available"}, status=status.HTTP_400_BAD_REQUEST)
    
    # FIX: 'daily_rate' era uma variável inexistente — o valor correto vem de car.daily_rate.
    # O objeto 'car' já foi buscado acima via database.get_car_by_id(), então basta acessar seu atributo.
    total_cost = float(car.daily_rate) * days
    
    # Aplicar desconto 
    if days > 7:
        total_cost = total_cost - (total_cost * 0.1)
    elif days > 3:
        total_cost = total_cost - (total_cost * 0.05)
    
    # Criar locação
    start_date = timezone.now()
    end_date = start_date + timedelta(days=days)
    
    rental = database.create_rental(
        car_id=car_id,
        customer_name=data['customer_name'],
        customer_email=data['customer_email'],
        start_date=start_date,
        end_date=end_date,
        total_cost=Decimal(str(total_cost))
    )
    
    # Marcar carro como indisponível
    car.available = False
    database.update_car(car)
    
    rental_serializer = RentalSerializer(rental)
    return Response(rental_serializer.data, status=status.HTTP_201_CREATED)


@api_view(['POST'])
def return_rental(request, rental_id):
    rental = database.get_rental_by_id(rental_id)
    
    if rental is None:
        return Response({"error": "Rental not found"}, status=status.HTTP_404_NOT_FOUND)
    
    if rental.returned == True:
        return Response({"error": "Car already returned"}, status=status.HTTP_400_BAD_REQUEST)
    
    # Marcar como retornado
    rental.returned = True
    rental.actual_return_date = timezone.now()

    # FIX: 'car' era definido dentro do bloco 'if' mas usado fora dele para marcar
    # o carro como disponível — movido para antes do bloco condicional para garantir
    # que está sempre acessível independentemente de haver ou não multa por atraso
    car = rental.car

    # Calcular multas de atraso
    if rental.actual_return_date > rental.end_date:
        late_days = (rental.actual_return_date - rental.end_date).days
        late_fee = float(car.daily_rate) * late_days * 1.5
        rental.late_fee = Decimal(str(late_fee))
        rental.total_cost = rental.total_cost + rental.late_fee

    database.update_rental(rental)

    # Marcar carro como disponível
    car.available = True
    database.update_car(car)

    # Integração com módulo rewards: crédito automático de pontos na devolução
    rewards = rewards_db.get_or_create_customer_rewards(rental.customer_email)
    points_earned = calculate_rental_points(rental, current_tier=rewards.tier)
    rental_days = max((rental.end_date - rental.start_date).days, 1)
    on_time = rental.actual_return_date <= rental.end_date
    reason = (
        f"Locação #{rental.id} — {rental_days} dia(s) com "
        f"{'devolução pontual' if on_time else 'devolução com atraso'}"
    )
    rewards_db.add_earned_points(rewards, rental, points_earned, reason)

    serializer = RentalSerializer(rental)
    return Response({
        "message": "Car returned successfully",
        "rental": serializer.data,
        "rewards": {
            "points_earned": points_earned,
            "total_points": rewards.total_points,
            "tier": rewards.tier,
        },
    })


@api_view(['GET'])
def get_rentals(request):
    rentals = database.get_all_rentals()
    serializer = RentalSerializer(rentals, many=True)
    return Response({"rentals": serializer.data})


@api_view(['GET'])
def get_customer_rentals(request, customer_email):
    """Obter locações para um cliente específico"""
    rentals = database.get_customer_rentals(customer_email)
    serializer = RentalSerializer(rentals, many=True)
    return Response({"rentals": serializer.data})


@api_view(['GET'])
def get_stats(request):
    stats = database.get_rental_stats()
    return Response(stats)

