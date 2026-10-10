"""Проверки существенных свойств учебного источника; каталог — тестовый."""

import copy
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from orders_lab.generator import Config, canonical_json, make_dataset, write_dataset
from orders_lab.validation import validate_snapshot


def fixture_catalog():
    return {"approval_status": "test_fixture", "categories": [{"id": 1, "name": "Тестовая категория"}],
            "products": [{"id": 1, "name": "Тестовый товар А", "category_id": 1, "base_price_rub": "100.00"},
                         {"id": 2, "name": "Тестовый товар Б", "category_id": 1, "base_price_rub": "200.00"}]}


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.config = Config(days=12, customers=8, products=2, orders=60)
        self.catalog = fixture_catalog()
        self.snapshots = make_dataset(self.config, self.catalog)

    def test_seed_reproducibility_and_change(self):
        self.assertEqual(canonical_json(self.snapshots), canonical_json(make_dataset(self.config, self.catalog)))
        other = make_dataset(Config(seed=43, days=12, customers=8, products=2, orders=60), self.catalog)
        self.assertNotEqual(canonical_json(self.snapshots), canonical_json(other))

    def test_every_snapshot_has_valid_relationships_and_amounts(self):
        for snapshot in self.snapshots:
            validate_snapshot(snapshot)
        self.assertEqual(len(self.snapshots[-1]["orders"]), 60)

    def test_history_and_purchase_price_stability(self):
        seen_prices = {s["products"][0]["catalog_price"] for s in self.snapshots}
        self.assertEqual(seen_prices, {"100.00", "110.00", "95.00"})
        old = [next(o for o in s["orders"] if o["id"] == 1) for s in self.snapshots]
        for order in old:
            self.assertEqual(order["items"], old[0]["items"])
            self.assertEqual(order["total_amount"], old[0]["total_amount"])
        new = next(o for o in self.snapshots[-1]["orders"] if o["id"] == 2)
        self.assertEqual(next(i["unit_price"] for i in new["items"] if i["product_id"] == 1), "110.00")

    def test_valid_status_transitions(self):
        allowed = {("created", "paid"), ("created", "cancelled"), ("paid", "shipped"),
                   ("paid", "cancelled"), ("shipped", "delivered")}
        previous = {}
        for snapshot in self.snapshots:
            for order in snapshot["orders"]:
                if order["id"] in previous and order["status"] != previous[order["id"]]:
                    self.assertIn((previous[order["id"]], order["status"]), allowed)
                previous[order["id"]] = order["status"]

    def test_incorrect_source_amount_is_rejected(self):
        snapshot = copy.deepcopy(self.snapshots[-1])
        snapshot["orders"][0]["total_amount"] = "0.01"
        with self.assertRaisesRegex(ValueError, "Сумма заказа"):
            validate_snapshot(snapshot)

    def test_future_record_is_rejected(self):
        snapshot = copy.deepcopy(self.snapshots[-1])
        snapshot["products"][0]["updated_at"] = snapshot["meta"]["observed_before"]
        with self.assertRaisesRegex(ValueError, "после даты снимка"):
            validate_snapshot(snapshot)

    def test_catalog_approval_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "ещё не согласован"):
                write_dataset(self.config, self.catalog, folder)
            self.assertFalse(list(Path(folder).iterdir()))


if __name__ == "__main__":
    unittest.main()
