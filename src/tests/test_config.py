import os
from pathlib import Path
import subprocess
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"


class ConfigLoadingTests(unittest.TestCase):
    def test_repo_root_launch_uses_src_dotenv_path(self):
        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONPATH": str(SOURCE_ROOT),
                "XTGTOK": "test-secret",
                "BOT_TOKEN": "123456:TEST_TOKEN_FOR_UNIT_TESTS",
                "DEFAULT_ADMIN_ID": "1",
                "GROUP_ID": "-1001",
                "CHANNEL_ID": "-1002",
                "BASE_URL": "https://example.test",
                "PROXY_URL": "socks5://127.0.0.1:2080",
            }
        )

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from core.config import Config; "
                    "print(Config.model_config['env_file'])"
                ),
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(
            Path(result.stdout.strip().splitlines()[-1]),
            SOURCE_ROOT / ".env",
        )


if __name__ == "__main__":
    unittest.main()
