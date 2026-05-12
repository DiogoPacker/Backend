# Utility functions for business logic
# Note: Some functions lack proper documentation

from datetime import datetime


def calculate_discount(days: int, total: float) -> float:
    """
    Calcular desconto baseado no número de dias de locação
    
    Args:
        days: Número de dias de locação
        total: Total do custo antes do desconto
    
    Returns:
        Valor do desconto a subtrair do total
    """
    if days > 7:
        return total * 0.1
    elif days > 3:
        return total * 0.05
    return 0


def calculate_late_fee(late_days: int, daily_rate: float) -> float:
    """
    Calcular multa por atraso na devolução (1.5x a diária por dia de atraso).
    """
    return late_days * daily_rate * 1.5


def validate_rental_dates(start_date: datetime, end_date: datetime) -> list[str]:
    """
    Validar datas de locação antes de persistir.

    FIX: a implementação original era um stub vazio ('pass') — qualquer par de
    datas seria aceito silenciosamente, incluindo fim anterior ao início.

    Returns:
        Lista de erros; vazia se as datas forem válidas.
    """
    errors = []
    if start_date is None or end_date is None:
        errors.append('As datas de início e fim são obrigatórias.')
        return errors
    if end_date <= start_date:
        errors.append('A data de devolução deve ser posterior à data de início.')
    return errors

