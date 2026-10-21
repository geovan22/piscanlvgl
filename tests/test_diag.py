import os
import sys
import unittest

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, APP_DIR)

import diag


class SudoersTests(unittest.TestCase):
    def test_parse_sudoers_extracts_absolute_paths(self):
        text = (
            "# comentario\n"
            "geo22 ALL=(ALL) NOPASSWD: /usr/bin/ip\n"
            "geo22 ALL=(ALL) NOPASSWD: /usr/sbin/iw\n"
            "\n"
        )
        self.assertEqual(diag.parse_sudoers(text), {"/usr/bin/ip", "/usr/sbin/iw"})

    def test_parse_sudoers_handles_comma_separated(self):
        text = "geo22 ALL=(ALL) NOPASSWD: /usr/bin/a, /usr/bin/b\n"
        self.assertEqual(diag.parse_sudoers(text), {"/usr/bin/a", "/usr/bin/b"})

    def test_missing_sudoers_reports_only_what_is_absent(self):
        required = ("x NOPASSWD: /usr/bin/ip\n"
                    "x NOPASSWD: /usr/sbin/iw\n"
                    "x NOPASSWD: /usr/bin/tcpdump\n")
        installed = "x NOPASSWD: /usr/bin/ip\n"
        self.assertEqual(
            diag.missing_sudoers(installed, required),
            ["/usr/bin/tcpdump", "/usr/sbin/iw"],
        )

    def test_classify_missing_splits_by_binary_presence(self):
        # 'hard' = el binario existe (falla real); 'soft' = no instalado (aviso).
        hard, soft = diag.classify_missing_sudoers(
            ["/usr/bin/present", "/usr/bin/absent"],
            exists_fn=lambda p: p.endswith("present"),
        )
        self.assertEqual(hard, ["/usr/bin/present"])
        self.assertEqual(soft, ["/usr/bin/absent"])


class PhyModesTests(unittest.TestCase):
    def test_parse_phy_modes_reads_section_and_stops(self):
        text = (
            "Wiphy phy2\n"
            "\tSupported interface modes:\n"
            "\t\t * IBSS\n"
            "\t\t * managed\n"
            "\t\t * AP\n"
            "\t\t * monitor\n"
            "\tBand 1:\n"
            "\t\t * no-deberia-contar\n"
        )
        self.assertEqual(
            diag.parse_phy_modes(text),
            {"IBSS", "managed", "AP", "monitor"},
        )


class BinaryCheckTests(unittest.TestCase):
    def test_statuses_for_present_missing_critical_and_optional(self):
        spec = [
            ("iw", ["/usr/sbin/iw"], "core", True),
            ("mdk4", ["/usr/sbin/mdk4"], "beacon", False),
            ("aircrack-ng", ["/usr/bin/aircrack-ng"], "audit", True),
        ]
        present = {"iw": "/usr/sbin/iw"}  # mdk4 y aircrack-ng faltan

        def resolver(name, candidates):
            return present.get(name)

        checks = diag.check_binaries(spec, resolver=resolver)
        by_name = {c["name"]: c["status"] for c in checks}
        self.assertEqual(by_name["iw"], "ok")          # presente
        self.assertEqual(by_name["mdk4"], "warn")      # opcional ausente
        self.assertEqual(by_name["aircrack-ng"], "fail")  # critico ausente

    def test_overall_ok_and_summary(self):
        checks = [
            {"status": "ok"}, {"status": "warn"}, {"status": "warn"},
        ]
        self.assertTrue(diag.overall_ok(checks))
        self.assertEqual(diag.summarize(checks), {"ok": 1, "warn": 2, "fail": 0})

        checks.append({"status": "fail"})
        self.assertFalse(diag.overall_ok(checks))
        self.assertEqual(diag.summarize(checks), {"ok": 1, "warn": 2, "fail": 1})


if __name__ == "__main__":
    unittest.main()
