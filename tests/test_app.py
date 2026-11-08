import unittest
from collections import Counter

from spr.app import register_modules
from tests.fakes import FakeClient


class RegisterModulesTest(unittest.TestCase):
    def test_every_handler_is_registered_once(self):
        client = FakeClient()
        help_modules = register_modules(client)
        names = [f"{callback.__module__}.{callback.__name__}" for callback, _ in client.handlers]
        self.assertEqual(len(names), 19)
        self.assertEqual(max(Counter(names).values()), 1)
        self.assertEqual(sorted(help_modules), ["devs", "info", "manage"])
