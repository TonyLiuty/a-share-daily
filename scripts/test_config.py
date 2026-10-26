"""Validate configuration without fetching data or changing strategy defaults."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from run_daily import DEFAULT_CONFIG, load_config


class ConfigTests(unittest.TestCase):
    def check_weights(self, weights):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            config = copy.deepcopy(DEFAULT_CONFIG)
            config['weights'] = weights
            path.write_text(json.dumps(config), encoding='utf-8')
            return load_config(path)

    def test_default_weights_unchanged(self):
        self.assertEqual(self.check_weights(DEFAULT_CONFIG['weights'])['weights'], DEFAULT_CONFIG['weights'])

    def test_negative_weight_even_when_sum_is_one(self):
        weights = dict(DEFAULT_CONFIG['weights'], momentum=-.1, trend=.65)
        with self.assertRaises(ValueError):
            self.check_weights(weights)

    def test_nonfinite_weight(self):
        for value in (float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.check_weights(dict(DEFAULT_CONFIG['weights'], momentum=value))


if __name__ == '__main__':
    unittest.main()
