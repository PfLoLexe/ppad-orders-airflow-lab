"""Проверки входного снимка перед переносом в DDS."""

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from .generator import STATUSES, midnight


def unique_rows(rows, label):
    result = {}
    for row in rows:
        key = row["id"]
        if type(key) is not int or key <= 0:
            raise ValueError(f"{label}: идентификатор должен быть положительным целым")
        if key in result:
            raise ValueError(f"{label}: повторный идентификатор {key}")
        result[key] = row
    return result


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Время должно содержать часовой пояс")
    return parsed


def positive_money(value):
    if not isinstance(value, str):
        raise ValueError("Денежное значение должно быть десятичной строкой")
    try:
        amount = Decimal(value)
    except InvalidOperation as e:
        raise ValueError("Некорректное денежное значение") from e
    if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
        raise ValueError("Цена должна быть положительной и содержать не более двух знаков")
    return amount


def validate_snapshot(snapshot):
    meta = snapshot["meta"]
    if meta["schema_version"] != 1 or meta["currency"] != "RUB" or meta["synthetic"] is not True:
        raise ValueError("Неподдерживаемый формат источника")
    day = date.fromisoformat(meta["snapshot_date"])
    cutoff = midnight(day + timedelta(days=1))
    if timestamp(meta["observed_before"]) != cutoff:
        raise ValueError("Снимок должен содержать данные до начала следующих суток")
    categories = unique_rows(snapshot["categories"], "categories")
    customers = unique_rows(snapshot["customers"], "customers")
    products = unique_rows(snapshot["products"], "products")
    orders = unique_rows(snapshot["orders"], "orders")
    for category in categories.values():
        if not isinstance(category["name"], str) or not category["name"].strip():
            raise ValueError("Пустое название категории")
    for customer in customers.values():
        for field in ("last_name", "first_name", "patronymic", "city"):
            if not isinstance(customer[field], str) or not customer[field].strip():
                raise ValueError(f"Пустое поле покупателя: {field}")
    for product in products.values():
        if product["category_id"] not in categories or not product["name"].strip():
            raise ValueError("Некорректная категория или название товара")
        positive_money(product["catalog_price"])
        if timestamp(product["updated_at"]) >= cutoff:
            raise ValueError("Товар изменён после даты снимка")
    for order in orders.values():
        if order["customer_id"] not in customers or order["status"] not in STATUSES:
            raise ValueError("Неизвестный покупатель или статус")
        created_at = timestamp(order["created_at"])
        changed_at = timestamp(order["status_updated_at"])
        if not created_at <= changed_at < cutoff:
            raise ValueError("Некорректная последовательность дат заказа")
        if not order["items"]:
            raise ValueError("Заказ без позиций")
        line_numbers = set()
        total = Decimal("0")
        for item in order["items"]:
            if type(item["line_no"]) is not int or item["line_no"] < 1 or item["line_no"] in line_numbers:
                raise ValueError("Повторный или некорректный номер позиции")
            line_numbers.add(item["line_no"])
            if item["product_id"] not in products:
                raise ValueError("Неизвестный товар в позиции")
            if type(item["quantity"]) is not int or item["quantity"] <= 0:
                raise ValueError("Количество должно быть положительным целым")
            total += positive_money(item["unit_price"]) * item["quantity"]
        if total != positive_money(order["total_amount"]):
            raise ValueError("Сумма заказа не совпадает с суммой позиций")
    return {"categories": len(categories), "customers": len(customers),
            "products": len(products), "orders": len(orders)}
