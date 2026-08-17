import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
VIGILANCIA = WORKFLOWS / "vigilancia.yml"
PRUEBA_TELEGRAM = WORKFLOWS / "prueba_telegram.yml"

CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_PYTHON_SHA = "5fda3b95a4ea91299a34e894583c3862153e4b97"


def read(path):
    return path.read_text(encoding="utf-8")


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vigilancia = read(VIGILANCIA)
        cls.prueba = read(PRUEBA_TELEGRAM)

    def test_both_workflows_are_manual_only(self):
        for text in (self.vigilancia, self.prueba):
            self.assertRegex(text, r"(?m)^\s{2}workflow_dispatch:\s*$")
            self.assertNotRegex(text, r"(?mi)^\s*(schedule|cron)\s*:")

    def test_official_actions_are_pinned_to_full_known_shas(self):
        for text in (self.vigilancia, self.prueba):
            self.assertIn(f"actions/checkout@{CHECKOUT_SHA}", text)
            self.assertIn(f"actions/setup-python@{SETUP_PYTHON_SHA}", text)
            for action, revision in re.findall(
                r"uses:\s*(actions/(?:checkout|setup-python))@([^\s]+)", text
            ):
                self.assertRegex(
                    revision,
                    r"^[0-9a-f]{40}$",
                    f"{action} debe estar fijada a un SHA completo.",
                )

    def test_vigilance_has_its_own_non_cancelling_concurrency_group(self):
        self.assertIn(
            "group: carreteras-cortadas-andalucia-vigilancia",
            self.vigilancia,
        )
        self.assertIn("cancel-in-progress: false", self.vigilancia)
        self.assertNotIn("incend", re.search(
            r"group:\s*([^\n]+)", self.vigilancia
        ).group(1).lower())

    def test_vigilance_uses_only_the_new_telegram_secrets(self):
        self.assertIn("secrets.TELEGRAM_BOT_TOKEN", self.vigilancia)
        self.assertIn("secrets.TELEGRAM_CHAT_ID", self.vigilancia)
        self.assertNotRegex(self.vigilancia, r"(?i)INFOCA")

    def test_tests_run_before_the_six_checks(self):
        tests = "python -m pytest -q"
        monitor = "python src/main.py"
        self.assertIn(tests, self.vigilancia)
        self.assertIn(monitor, self.vigilancia)
        self.assertLess(self.vigilancia.index(tests), self.vigilancia.index(monitor))
        self.assertRegex(
            self.vigilancia,
            r"for comprobacion in 1 2 3 4 5 6; do",
        )
        self.assertIn("sleep 900", self.vigilancia)
        timeout = re.search(
            r"(?s)vigilar:.*?timeout-minutes:\s*(\d+)", self.vigilancia
        )
        self.assertIsNotNone(timeout)
        self.assertGreaterEqual(int(timeout.group(1)), 90)

    def test_state_is_loaded_from_and_saved_to_separate_branch(self):
        self.assertIn("STATE_BRANCH: estado", self.vigilancia)
        self.assertIn("STATE_FILE: data/state.json", self.vigilancia)
        self.assertIn("git ls-remote --exit-code --heads", self.vigilancia)
        self.assertIn('if [[ "${lookup_status}" -ne 2 ]]', self.vigilancia)
        self.assertIn("switch --orphan", self.vigilancia)
        self.assertIn("python -m json.tool", self.vigilancia)
        self.assertIn("git -C \"${STATE_WORKTREE}\" add -f", self.vigilancia)
        self.assertIn("git -C \"${STATE_WORKTREE}\" commit", self.vigilancia)
        self.assertEqual(
            self.vigilancia.count(
                'git -C "${STATE_WORKTREE}" push origin "HEAD:${STATE_BRANCH}"'
            ),
            1,
            "Debe existir un unico push de estado, condicionado a un cambio.",
        )
        diff = self.vigilancia.index("diff --cached --quiet")
        commit = self.vigilancia.index('git -C "${STATE_WORKTREE}" commit')
        push = self.vigilancia.index('git -C "${STATE_WORKTREE}" push')
        self.assertLess(diff, commit)
        self.assertLess(commit, push)

    def test_relay_always_runs_except_after_cancellation(self):
        self.assertIn("needs: vigilar", self.vigilancia)
        self.assertIn("if: ${{ always() && !cancelled() }}", self.vigilancia)
        self.assertIn(
            "if: ${{ needs.vigilar.result == 'failure' }}",
            self.vigilancia,
        )
        self.assertIn("gh workflow run vigilancia.yml", self.vigilancia)
        relay = self.vigilancia.split("  relevo:", 1)[1]
        self.assertEqual(relay.count("sleep 900"), 1)

    def test_telegram_test_is_unmistakably_for_the_new_bot(self):
        self.assertIn("PRUEBA CORRECTA DEL BOT NUEVO", self.prueba)
        self.assertIn("Carreteras cortadas - Andalucía", self.prueba)
        self.assertIn("todos los motivos", self.prueba)
        self.assertIn("Canal general de cortes completos", self.prueba)
        self.assertNotRegex(self.prueba, r"(?i)INFOCA|incendios")
        self.assertIn("secrets.TELEGRAM_BOT_TOKEN", self.prueba)
        self.assertIn("secrets.TELEGRAM_CHAT_ID", self.prueba)


if __name__ == "__main__":
    unittest.main()

