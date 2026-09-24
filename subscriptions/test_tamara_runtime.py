from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase

from deploy import tamara_runtime


class TamaraRuntimeTests(SimpleTestCase):
    def test_prepare_and_activate_preserve_source_and_unrelated_settings(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.env"
            target = root / ".env.production"
            source_text = (
                "TAMARA_ENABLED=True\nTAMARA_ENVIRONMENT=production\n"
                "TAMARA_API_TOKEN=live-token-abcdefghijklmnopqrstuvwxyz\n"
                "TAMARA_NOTIFICATION_TOKEN=live-notification-abcdefghijklmnopqrstuvwxyz\n"
            )
            source.write_text(source_text, encoding="utf-8")
            target.write_text("# Keep this comment\nOTHER_SETTING=unchanged\nTAMARA_ENABLED=False\n", encoding="utf-8")

            with patch.object(tamara_runtime, "SOURCE", source), patch.object(tamara_runtime, "TARGET", target):
                first_backup = root / ".env.production.bak.tamara-prepare"
                tamara_runtime.apply("prepare", first_backup)
                prepared = tamara_runtime.parse_env(target.read_text(encoding="utf-8"))
                self.assertEqual(prepared["TAMARA_ENABLED"], "False")
                self.assertEqual(prepared["TAMARA_API_BASE_URL"], "https://api.tamara.co")
                self.assertEqual(prepared["OTHER_SETTING"], "unchanged")
                self.assertIn("# Keep this comment", target.read_text(encoding="utf-8"))
                self.assertEqual(
                    first_backup.read_text(encoding="utf-8"),
                    "# Keep this comment\nOTHER_SETTING=unchanged\nTAMARA_ENABLED=False\n",
                )

                second_backup = root / ".env.production.bak.tamara-activate"
                tamara_runtime.apply("activate", second_backup)
                activated = tamara_runtime.parse_env(target.read_text(encoding="utf-8"))
                self.assertEqual(activated["TAMARA_ENABLED"], "True")
                self.assertEqual(
                    tamara_runtime.parse_env(second_backup.read_text(encoding="utf-8"))["TAMARA_ENABLED"],
                    "False",
                )
                self.assertEqual(source.read_text(encoding="utf-8"), source_text)

    def test_activation_requires_prepared_credentials(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.env"
            target = root / ".env.production"
            backup = root / ".env.production.bak.tamara-activate"
            source.write_text(
                "TAMARA_ENABLED=True\nTAMARA_ENVIRONMENT=production\n"
                "TAMARA_API_TOKEN=live-token-abcdefghijklmnopqrstuvwxyz\n"
                "TAMARA_NOTIFICATION_TOKEN=live-notification-abcdefghijklmnopqrstuvwxyz\n",
                encoding="utf-8",
            )
            target.write_text("TAMARA_ENABLED=False\nTAMARA_ENVIRONMENT=sandbox\n", encoding="utf-8")
            with patch.object(tamara_runtime, "SOURCE", source), patch.object(tamara_runtime, "TARGET", target):
                with self.assertRaisesRegex(RuntimeError, "Prepare the live credentials"):
                    tamara_runtime.apply("activate", backup)
            self.assertFalse(backup.exists())
            self.assertEqual(tamara_runtime.parse_env(target.read_text(encoding="utf-8"))["TAMARA_ENABLED"], "False")
