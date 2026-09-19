import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot"
WEB = ROOT / "website"


class PublicReleaseTests(unittest.TestCase):
    def test_python_sources_compile(self):
        for path in ROOT.rglob("*.py"):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def test_no_hardcoded_blocklist_or_control_domain(self):
        runtime = (BOT / "ceylan_runtime.py").read_text(encoding="utf-8")
        config = (BOT / "project_config.py").read_text(encoding="utf-8")
        self.assertNotIn("BLOCKED = {'", runtime)
        self.assertIn('BLOCKED_GROUP_IDS = csv_set("BLOCKED_GROUP_IDS")', config)
        self.assertIn('PUBLIC_BASE_URL = os.getenv("BOT_CONTROL_BASE_URL", "")', config)
        self.assertNotIn('PUBLIC_BASE_URL = "http', config)

    def test_blocklist_is_environment_driven_and_not_in_snapshot(self):
        code = """
import json, os
os.environ['BLOCKED_GROUP_IDS']='10001,10002'
os.environ['CEYLAN_BOT_SNAPSHOT']=os.environ['OUT']
import ceylan_runtime
ceylan_runtime.snapshot()
print(json.dumps(json.load(open(os.environ['OUT']))))
"""
        with tempfile.TemporaryDirectory() as folder:
            env = dict(os.environ, PYTHONPATH=str(BOT), OUT=str(Path(folder) / "snapshot.json"))
            result = subprocess.run([sys.executable, "-c", code], env=env, text=True, capture_output=True, check=True)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["blocklist_enabled"])
        self.assertEqual(payload["blocked_group_count"], 2)
        self.assertNotIn("blocked_groups", payload)
        self.assertNotIn("10001", result.stdout)

    def test_branding_is_configurable(self):
        code = """
import json, os
os.environ['BOT_DISPLAY_NAME']='Custom Bot'
os.environ['BOT_PERSONA_NAME']='Custom Persona'
os.environ['BOT_TRIGGER_WORDS']='hello,assistant'
import project_config
print(json.dumps({'name':project_config.BOT_DISPLAY_NAME,'persona':project_config.BOT_PERSONA_NAME,'triggers':sorted(project_config.BOT_TRIGGER_WORDS)}))
"""
        env = dict(os.environ, PYTHONPATH=str(BOT))
        payload = json.loads(subprocess.run([sys.executable, "-c", code], env=env, text=True, capture_output=True, check=True).stdout)
        self.assertEqual(payload, {"name": "Custom Bot", "persona": "Custom Persona", "triggers": ["assistant", "hello"]})

    def test_endpoint_requires_configuration(self):
        code = """
import os
os.environ.pop('BOT_CONTROL_BASE_URL',None)
os.environ.pop('CEYLAN_HEARTBEAT_URL',None)
from project_config import api_url
try: api_url('/api/bot/status','CEYLAN_HEARTBEAT_URL')
except RuntimeError: print('ok')
"""
        env = dict(os.environ, PYTHONPATH=str(BOT))
        output = subprocess.run([sys.executable, "-c", code], env=env, text=True, capture_output=True, check=True).stdout.strip()
        self.assertEqual(output, "ok")


if __name__ == "__main__":
    unittest.main()
