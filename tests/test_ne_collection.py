from __future__ import annotations

import unittest

from netx_api.collection_service import _parse_commands


class NeCollectionParseTests(unittest.TestCase):
    def test_parse_commands_skips_comments(self):
        cmds = _parse_commands("display version\n# comment\ndisplay ip int brief\n")
        self.assertEqual(cmds, ["display version", "display ip int brief"])


if __name__ == "__main__":
    unittest.main()
