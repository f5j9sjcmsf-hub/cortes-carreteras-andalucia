from __future__ import annotations

from copy import deepcopy
import unittest

from src.logic import (
    EVENT_CLOSED,
    UnsupportedStateVersion,
    format_message,
    plan_changes,
    plan_initial,
    reconcile,
)


NOW_1 = "2026-08-17T20:00:00Z"
NOW_2 = "2026-08-17T20:15:00Z"
NOW_3 = "2026-08-17T20:30:00Z"


def closure(**overrides):
    item = {
        "source_ids": ["source-1"],
        "situation_ids": ["situation-1"],
        "record_ids": ["record-1"],
        "province": "Granada",
        "localities": ["Güéjar Sierra"],
        "road": "A-395",
        "km_start": 31,
        "km_end": 39,
        "direction": "both",
        "reason": "Desprendimiento",
        "cause_code": "infrastructureDamageObstruction",
        "detail_code": "rockfalls",
    }
    item.update(overrides)
    return item


def baseline(item=None):
    messages, state = plan_initial({}, [item or closure()], NOW_1)
    if len(messages) != 1:
        raise AssertionError("the fixture baseline must contain one closure")
    return state


def only_active(state):
    if len(state["active"]) != 1:
        raise AssertionError("the test expected exactly one active closure")
    return next(iter(state["active"].values()))


class ReconciliationTests(unittest.TestCase):
    def test_initial_run_publishes_complete_snapshot_in_deterministic_order(self):
        granada = closure()
        almeria = closure(
            source_ids=["source-2"],
            situation_ids=["situation-2"],
            record_ids=["record-2"],
            province="Almería",
            localities=["Níjar"],
            road="A-7",
            km_start="481,5",
            km_end="481,5",
            direction="increasing",
            reason="Obras",
        )
        original = deepcopy([granada, almeria])

        messages, state = plan_initial({}, [granada, almeria], NOW_1)
        reversed_messages, _ = plan_initial({}, [almeria, granada], NOW_1)

        self.assertEqual(messages, reversed_messages)
        self.assertEqual(len(messages), 2)
        self.assertIn("📍 Almería — Níjar", messages[0])
        self.assertIn("<b>🔴 CARRETERA CORTADA</b>", messages[0])
        self.assertIn("Vía: A-7", messages[0])
        self.assertIn("Sentido: Creciente", messages[0])
        self.assertIn("Kilómetros: 481,500", messages[0])
        self.assertIn("📍 Granada — Güéjar Sierra", messages[1])
        self.assertIn("Kilómetros: 31,000–39,000", messages[1])
        self.assertEqual(state["version"], 1)
        self.assertIs(state["initialized"], True)
        self.assertEqual(state["revision"], 1)
        self.assertEqual(state["initialized_at"], NOW_1)
        self.assertEqual(len(state["active"]), 2)
        self.assertEqual([granada, almeria], original)

    def test_initial_function_does_not_republish_when_state_is_initialized(self):
        state = baseline()
        messages, next_state = plan_initial(state, [closure()], NOW_2)
        self.assertEqual(messages, [])
        self.assertEqual(next_state["revision"], 2)

    def test_unchanged_poll_is_silent_and_inputs_remain_immutable(self):
        state = baseline()
        incoming = closure()
        original_state = deepcopy(state)
        original_incoming = deepcopy(incoming)

        messages, next_state = plan_changes(state, [incoming], NOW_2)

        self.assertEqual(messages, [])
        self.assertEqual(state, original_state)
        self.assertEqual(incoming, original_incoming)
        active = only_active(next_state)
        self.assertEqual(active["first_seen_at"], NOW_1)
        self.assertEqual(active["last_changed_at"], NOW_1)
        self.assertEqual(active["last_seen_at"], NOW_2)

    def test_new_closure_after_baseline_is_announced(self):
        state = baseline()
        new = closure(
            source_ids=["new-source"],
            situation_ids=["new-situation"],
            province="Jaén",
            localities=["Cazorla"],
            road="A-319",
            km_start=12,
            km_end=15,
            direction="decreasing",
            reason="Daños en la vía",
        )
        messages, next_state = plan_changes(state, [closure(), new], NOW_2)
        self.assertEqual(len(messages), 1)
        self.assertIn("🔴 CARRETERA CORTADA", messages[0])
        self.assertIn("📍 Jaén — Cazorla", messages[0])
        self.assertIn("Sentido: Decreciente", messages[0])
        self.assertEqual(len(next_state["active"]), 2)

    def test_every_user_visible_field_change_is_announced_as_update(self):
        cases = [
            ("province", "Málaga", "📍 Málaga — Güéjar Sierra"),
            ("localities", ["Monachil"], "📍 Granada — Monachil"),
            ("reason", "Obras", "\nObras\n"),
            ("road", "A-92", "Vía: A-92"),
            ("direction", "increasing", "Sentido: Creciente"),
            ("km_start", 32, "Kilómetros: 32,000–39,000"),
            ("km_end", 40, "Kilómetros: 31,000–40,000"),
        ]
        for field, value, expected in cases:
            with self.subTest(field=field):
                state = baseline()
                changed = closure(**{field: value})
                messages, next_state = plan_changes(state, [changed], NOW_2)
                self.assertEqual(len(messages), 1)
                self.assertIn("🟠 CORTE ACTUALIZADO", messages[0])
                self.assertIn(expected, messages[0])
                self.assertEqual(only_active(next_state)["last_changed_at"], NOW_2)

    def test_identifier_continuity_makes_many_changes_one_update(self):
        state = baseline()
        changed = closure(
            province="Málaga",
            localities=["Ronda"],
            road="A-397",
            km_start=15,
            km_end=20,
            reason="Obras",
            direction="decreasing",
        )
        messages, next_state = plan_changes(state, [changed], NOW_2)
        self.assertEqual(len(messages), 1)
        self.assertIn("CORTE ACTUALIZADO", messages[0])
        self.assertNotIn("CARRETERA REABIERTA", messages[0])
        self.assertEqual(len(next_state["active"]), 1)

    def test_source_identity_has_priority_over_shared_situation_identity(self):
        first = closure(source_ids=["A"], situation_ids=["shared"], road="A-1")
        second = closure(
            source_ids=["B"],
            situation_ids=["shared"],
            road="A-2",
            km_start=50,
            km_end=55,
        )
        _, state = plan_initial({}, [first, second], NOW_1)
        current_first = closure(
            source_ids=["A"], situation_ids=["shared"], road="A-9"
        )
        current_second = closure(
            source_ids=["B"],
            situation_ids=["shared"],
            road="A-8",
            km_start=50,
            km_end=55,
        )
        messages, next_state = plan_changes(
            state, [current_second, current_first], NOW_2
        )
        self.assertEqual(len(messages), 2)
        self.assertTrue(all("CORTE ACTUALIZADO" in message for message in messages))
        observed = {
            entry["source_ids"][0]: entry["road"]
            for entry in next_state["active"].values()
        }
        self.assertEqual(observed, {"A": "A-9", "B": "A-8"})

    def test_exact_segment_fallback_avoids_close_and_reopen_when_ids_change(self):
        state = baseline()
        changed_ids = closure(
            source_ids=["replacement-source"],
            situation_ids=["replacement-situation"],
            record_ids=["replacement-record"],
            localities=["Sierra Nevada"],
            reason="Daños en la vía",
        )
        messages, next_state = plan_changes(state, [changed_ids], NOW_2)
        self.assertEqual(len(messages), 1)
        self.assertIn("CORTE ACTUALIZADO", messages[0])
        self.assertNotIn("CARRETERA REABIERTA", messages[0])
        self.assertEqual(
            only_active(next_state)["source_ids"], ["replacement-source"]
        )

    def test_fallback_requires_exact_kilometre_range(self):
        state = baseline()
        replacement = closure(
            source_ids=["replacement-source"],
            situation_ids=["replacement-situation"],
            km_start=31,
            km_end=40,
        )
        messages, next_state = plan_changes(state, [replacement], NOW_2)
        self.assertEqual(len(messages), 2)
        self.assertTrue(any("CARRETERA CORTADA" in message for message in messages))
        self.assertTrue(any("CARRETERA REABIERTA" in message for message in messages))
        self.assertEqual(len(next_state["active"]), 1)

    def test_partial_reopening_with_overlapping_ids(self):
        state = baseline(
            closure(
                source_ids=["increasing-record", "decreasing-record"],
                direction="both",
            )
        )
        remaining = closure(
            source_ids=["increasing-record"], direction="increasing"
        )
        messages, next_state = plan_changes(state, [remaining], NOW_2)
        self.assertEqual(len(messages), 1)
        self.assertIn("🟠 REAPERTURA PARCIAL", messages[0])
        self.assertIn("Sentido: Creciente", messages[0])
        self.assertEqual(only_active(next_state)["direction"], "increasing")

    def test_direction_reduction_without_id_overlap_is_regular_update(self):
        state = baseline(
            closure(source_ids=["old"], situation_ids=["old-situation"])
        )
        remaining = closure(
            source_ids=["new"],
            situation_ids=["new-situation"],
            direction="decreasing",
        )
        messages, _ = plan_changes(state, [remaining], NOW_2)
        self.assertEqual(len(messages), 1)
        self.assertIn("🟠 CORTE ACTUALIZADO", messages[0])
        self.assertNotIn("REAPERTURA PARCIAL", messages[0])

    def test_direction_label_change_with_same_ids_is_regular_update(self):
        identifiers = ["increasing-record", "decreasing-record"]
        state = baseline(closure(source_ids=identifiers, direction="both"))
        corrected = closure(source_ids=identifiers, direction="increasing")
        messages, _ = plan_changes(state, [corrected], NOW_2)
        self.assertEqual(len(messages), 1)
        self.assertIn("🟠 CORTE ACTUALIZADO", messages[0])
        self.assertNotIn("REAPERTURA PARCIAL", messages[0])

    def test_disappearing_closure_is_total_reopening_and_is_removed(self):
        state = baseline()
        messages, next_state = plan_changes(state, [], NOW_2)
        self.assertEqual(len(messages), 1)
        self.assertIn("🟢 CARRETERA REABIERTA", messages[0])
        self.assertIn("Vía: A-395", messages[0])
        self.assertEqual(next_state["active"], {})

    def test_reclosure_after_total_reopening_is_announced_again(self):
        state = baseline()
        _, reopened_state = plan_changes(state, [], NOW_2)
        messages, reclosed_state = plan_changes(reopened_state, [closure()], NOW_3)
        self.assertEqual(len(messages), 1)
        self.assertIn("🔴 CARRETERA CORTADA", messages[0])
        self.assertEqual(only_active(reclosed_state)["first_seen_at"], NOW_3)

    def test_internal_id_and_code_refresh_is_silent(self):
        state = baseline()
        refreshed = closure(
            source_ids=["source-1", "new-source-alias"],
            record_ids=["new-record"],
            cause_code="new-internal-code",
            detail_code="new-detail-code",
        )
        messages, next_state = plan_changes(state, [refreshed], NOW_2)
        self.assertEqual(messages, [])
        active = only_active(next_state)
        self.assertEqual(active["record_ids"], ["new-record"])
        self.assertEqual(active["cause_code"], "new-internal-code")

    def test_duplicate_feed_rows_merge_identifiers(self):
        duplicate = closure(
            source_ids=["source-2"],
            situation_ids=["situation-2"],
            record_ids=["record-2"],
        )
        messages, state = plan_initial({}, [closure(), duplicate], NOW_1)
        self.assertEqual(len(messages), 1)
        active = only_active(state)
        self.assertEqual(active["source_ids"], ["source-1", "source-2"])
        self.assertEqual(active["situation_ids"], ["situation-1", "situation-2"])
        self.assertEqual(active["record_ids"], ["record-1", "record-2"])

    def test_reversed_numeric_segment_is_canonical_and_silent(self):
        state = baseline()
        reversed_segment = closure(km_start="39,000", km_end="31,000")
        messages, _ = plan_changes(state, [reversed_segment], NOW_2)
        self.assertEqual(messages, [])

    def test_html_is_escaped_and_missing_values_have_safe_labels(self):
        message = format_message(
            closure(
                province="Cádiz & Málaga",
                localities=["<Ronda>"],
                road="",
                km_start=None,
                km_end=None,
                direction="not-a-datex-direction",
                reason="Obras <urgentes>",
            ),
            EVENT_CLOSED,
        )
        self.assertIn("Cádiz &amp; Málaga — &lt;Ronda&gt;", message)
        self.assertIn("Obras &lt;urgentes&gt;", message)
        self.assertIn("Vía: No indicada", message)
        self.assertIn("Sentido: No indicado", message)
        self.assertIn("Kilómetros: No indicados", message)

    def test_spanish_dgt_signed_directions_use_the_profile_semantics(self):
        negative = format_message(
            closure(direction="negative"), EVENT_CLOSED
        )
        positive = format_message(
            closure(direction="positive"), EVENT_CLOSED
        )
        self.assertIn("Sentido: Creciente", negative)
        self.assertIn("Sentido: Decreciente", positive)

    def test_reconcile_selects_initial_then_incremental_mode(self):
        initial_messages, state = reconcile(None, [closure()], NOW_1)
        later_messages, _ = reconcile(state, [closure()], NOW_2)
        self.assertEqual(len(initial_messages), 1)
        self.assertEqual(later_messages, [])

    def test_unsupported_state_version_is_rejected_without_mutation(self):
        state = {"version": 999, "initialized": True, "active": {}}
        original = deepcopy(state)
        with self.assertRaises(UnsupportedStateVersion):
            plan_changes(state, [], NOW_2)
        self.assertEqual(state, original)


if __name__ == "__main__":
    unittest.main()

