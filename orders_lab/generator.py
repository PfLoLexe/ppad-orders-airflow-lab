"""Воспроизводимые ежедневные снимки вымышленного магазина.

Названия и исходные цены поступают из согласованного каталога. Денежные
значения сериализуются строками с двумя знаками, без арифметики float.
"""

import argparse
import copy
import hashlib
import json
import random
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

MSK = timezone(timedelta(hours=3))
STATUSES = ("created", "paid", "shipped", "delivered", "cancelled")
STATUS_LABELS = dict(zip(STATUSES, ("Создан", "Оплачен", "Отправлен", "Доставлен", "Отменён")))
CITIES = ("Москва", "Санкт-Петербург", "Казань", "Екатеринбург", "Новосибирск")
MALE_NAMES = ("Александр", "Валентин", "Валерий", "Кирилл", "Никита", "Степан")
FEMALE_NAMES = ("Алла", "Анна", "Валерия", "Полина")
# Ручные формы исключают ошибки вроде «Никитаович». Леммы взяты из
# сохранённых категорий Викиновостей; женские/мужские формы согласованы.
PATRONYMICS = (
    ("Александрович", "Александровна"), ("Валентинович", "Валентиновна"),
    ("Валерьевич", "Валерьевна"), ("Кириллович", "Кирилловна"),
    ("Никитич", "Никитична"), ("Степанович", "Степановна"),
)
SURNAMES = (
    ("Ефимов", "Ефимова"), ("Логинов", "Логинова"),
    ("Морозов", "Морозова"), ("Пономарёв", "Пономарёва"),
    ("Субботин", "Субботина"), ("Фокин", "Фокина"),
    ("Путин", "Путина"), ("Вагин", "Вагина"),
)


def canonical_json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def money(value):
    return format(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f")


def midnight(day):
    return datetime.combine(day, time.min, tzinfo=MSK)


@dataclass(frozen=True)
class Config:
    seed: int = 42
    start_date: str = "2026-09-01"
    days: int = 30
    customers: int = 100
    products: int = 30
    orders: int = 1000

    def validate(self, catalog):
        date.fromisoformat(self.start_date)
        if self.days < 4 or self.customers < 1 or self.orders < 3:
            raise ValueError("Нужно не менее 4 дней, 1 покупателя и 3 заказов")
        if not 1 <= self.products <= len(catalog["products"]):
            raise ValueError("Число товаров должно быть в пределах согласованного каталога")


def product_versions(config, catalog):
    """Два гарантированных изменения цены первого товара для SCD2."""
    start = date.fromisoformat(config.start_date)
    first_change = max(1, config.days // 3)
    second_change = min(config.days - 1, max(first_change + 1, 2 * config.days // 3))
    result = {}
    for row in catalog["products"][:config.products]:
        base = {
            "id": row["id"], "name": row["name"], "category_id": row["category_id"],
            "catalog_price": money(row["base_price_rub"]),
            "updated_at": midnight(start).isoformat(),
        }
        versions = [base]
        if row["id"] == catalog["products"][0]["id"]:
            for day_offset, multiplier in ((first_change, "1.10"), (second_change, "0.95")):
                version = dict(base)
                version["catalog_price"] = money(Decimal(base["catalog_price"]) * Decimal(multiplier))
                version["updated_at"] = midnight(start + timedelta(days=day_offset)).isoformat()
                versions.append(version)
        result[row["id"]] = versions
    return result


def version_at(versions, timestamp):
    eligible = [v for v in versions if datetime.fromisoformat(v["updated_at"]) <= timestamp]
    return copy.deepcopy(eligible[-1])


def make_dataset(config, catalog):
    """Чистая функция; сеть и база данных не требуются."""
    config.validate(catalog)
    rng = random.Random(config.seed)
    start = date.fromisoformat(config.start_date)
    versions = product_versions(config, catalog)
    product_ids = list(versions)
    categories = [dict(c) for c in catalog["categories"]]
    dataset_id = "orders-" + digest({"config": asdict(config), "catalog": catalog})[:16]
    customers = []
    for customer_id in range(1, config.customers + 1):
        gender = rng.randrange(2)
        last_name = rng.choice(SURNAMES)[gender]
        first_name = rng.choice(MALE_NAMES if gender == 0 else FEMALE_NAMES)
        patronymic = rng.choice(PATRONYMICS)[gender]
        customers.append({
            "id": customer_id, "last_name": last_name, "first_name": first_name,
            "patronymic": patronymic, "city": rng.choice(CITIES),
        })
    orders = []
    first_change = max(1, config.days // 3)
    for order_id in range(1, config.orders + 1):
        offset = rng.randrange(config.days)
        if order_id == 1:
            offset = 0
        elif order_id == 2:
            offset = min(first_change + 1, config.days - 1)
        created_at = midnight(start + timedelta(days=offset)) + timedelta(hours=12, minutes=rng.randrange(60))
        chosen = rng.sample(product_ids, rng.randint(1, min(4, len(product_ids))))
        if order_id in (1, 2) and product_ids[0] not in chosen:
            chosen[0] = product_ids[0]
        items = []
        for line_no, product_id in enumerate(chosen, 1):
            product = version_at(versions[product_id], created_at)
            items.append({"line_no": line_no, "product_id": product_id,
                          "quantity": rng.randint(1, 3), "unit_price": product["catalog_price"]})
        events = [(created_at, "created")]
        paid_at = midnight(created_at.date() + timedelta(days=rng.randint(1, 2)))
        # Первые два заказа гарантированно доставляются; третий отменяется.
        cancel = order_id == 3 or (order_id > 3 and rng.random() < 0.15)
        if cancel and rng.random() < 0.5:
            events.append((paid_at, "cancelled"))
        else:
            events.append((paid_at, "paid"))
            next_at = paid_at + timedelta(days=rng.randint(1, 2))
            if cancel:
                events.append((next_at, "cancelled"))
            else:
                events.append((next_at, "shipped"))
                events.append((next_at + timedelta(days=rng.randint(1, 3)), "delivered"))
        total = sum((Decimal(i["unit_price"]) * i["quantity"] for i in items), Decimal("0"))
        orders.append({"id": order_id, "customer_id": rng.randint(1, config.customers),
                       "created_at": created_at.isoformat(), "total_amount": money(total),
                       "items": items, "events": events})
    snapshots = []
    for offset in range(config.days):
        day = start + timedelta(days=offset)
        cutoff = midnight(day + timedelta(days=1))
        observed_orders = []
        for row in orders:
            if datetime.fromisoformat(row["created_at"]) >= cutoff:
                continue
            visible = [(at, state) for at, state in row["events"] if at < cutoff]
            at, state = visible[-1]
            order = {k: copy.deepcopy(v) for k, v in row.items() if k != "events"}
            order.update(status=state, status_updated_at=at.isoformat())
            observed_orders.append(order)
        products = [version_at(v, cutoff - timedelta(microseconds=1)) for v in versions.values()]
        snapshots.append({
            "meta": {"schema_version": 1, "dataset_id": dataset_id,
                     "snapshot_date": day.isoformat(), "observed_before": cutoff.isoformat(),
                     "currency": "RUB", "synthetic": True},
            "categories": categories, "customers": customers,
            "products": products, "orders": observed_orders,
        })
    return snapshots


def write_dataset(config, catalog, output):
    if catalog.get("approval_status") != "approved":
        raise ValueError("Каталог ещё не согласован; генерация рабочего набора запрещена")
    snapshots = make_dataset(config, catalog)
    dataset_id = snapshots[0]["meta"]["dataset_id"]
    folder = Path(output) / dataset_id
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "dataset_id": dataset_id,
                "config": asdict(config), "catalog_sha256": digest(catalog), "snapshots": []}
    for snapshot in snapshots:
        name = snapshot["meta"]["snapshot_date"] + ".json"
        content = canonical_json(snapshot)
        target = folder / name
        if target.exists() and target.read_bytes() != content:
            raise ValueError(f"Снимок {target} уже существует с другим содержимым")
        target.write_bytes(content)
        manifest["snapshots"].append({"date": snapshot["meta"]["snapshot_date"],
                                      "file": name, "sha256": hashlib.sha256(content).hexdigest()})
    target = folder / "manifest.json"
    target.write_bytes(canonical_json(manifest))
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("data/snapshots"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--start-date", default="2026-09-01")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--customers", type=int, default=100)
    parser.add_argument("--products", type=int, default=30)
    parser.add_argument("--orders", type=int, default=1000)
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    config = Config(**{k: getattr(args, k) for k in Config.__dataclass_fields__})
    print(write_dataset(config, catalog, args.output))


if __name__ == "__main__":
    main()
