import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from modules import ai_twin as ai
from test_ai_twin_status import ReplyTests
from core import services


class RuntimeClockTests(unittest.TestCase):
    def test_minsk_date_time_and_offset(self):
        clock = ai.runtime_clock_instruction('Europe/Minsk', datetime(2026, 10, 19, 20, 56, 30, tzinfo=timezone.utc))
        for value in ['2026-10-19', '23:56:30', 'Monday', 'Europe/Minsk', '+0300', '20:56:30+00:00']:
            self.assertIn(value, clock)

    def test_local_day_and_year_roll_over(self):
        clock = ai.runtime_clock_instruction('Europe/Minsk', datetime(2026, 12, 31, 22, 5, tzinfo=timezone.utc))
        self.assertIn('Current local date: 2027-01-01', clock)
        self.assertIn('01:05:00', clock)
        self.assertIn('Friday', clock)

    def test_configured_zone_instead_of_container_timezone(self):
        clock = ai.runtime_clock_instruction('Asia/Tokyo', datetime(2026, 10, 19, 20, 56, tzinfo=timezone.utc))
        self.assertIn('Current local date: 2026-10-20', clock)
        self.assertIn('05:56:00', clock)
        self.assertIn('+0900', clock)

    def test_bad_timezone_has_explicit_utc_fallback(self):
        clock = ai.runtime_clock_instruction('Invalid/Timezone', datetime(2026, 10, 19, 20, 56, tzinfo=timezone.utc))
        self.assertIn('Timezone: UTC', clock)
        self.assertIn('20:56:00', clock)
        self.assertIn('+0000', clock)

    def test_naive_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            ai.runtime_clock_instruction('Europe/Minsk', datetime(2026, 10, 19, 20, 56))

    def test_clock_is_sent_in_system_instruction(self):
        instruction = ai.runtime_clock_instruction('Europe/Minsk')
        config = services._build_config(instruction, False)
        self.assertEqual(config.system_instruction, instruction)
        self.assertIn('takes precedence', instruction)


class ReplyClockTests(ReplyTests):
    async def test_clock_is_computed_after_media_context(self):
        self.g['tz'] = 'Europe/Minsk'
        events = []

        async def context(*args, **kwargs):
            events.append('context-ready')
            return 'Old mistaken date: 24 May 2026', [], 0, False

        def clock(tz_name):
            self.assertEqual(events, ['context-ready'])
            self.assertEqual(tz_name, 'Europe/Minsk')
            events.append('fresh-clock')
            return 'Authoritative date: 2026-10-19, time: 23:56:30, Europe/Minsk'

        ai.build_dialog_context.side_effect = context
        with patch.object(ai, 'runtime_clock_instruction', side_effect=clock):
            self.assertEqual(await self.run_reply(), 'sent')
        kwargs = ai.generate_ai_response.await_args.kwargs
        self.assertIn('2026-10-19', kwargs['custom_prompt'])
        self.assertIn('23:56:30', kwargs['custom_prompt'])
        self.assertEqual(events, ['context-ready', 'fresh-clock'])

    async def test_forced_reply_includes_current_clock(self):
        self.g['tz'] = 'Europe/Minsk'
        self.assertEqual(await self.run_reply(force=True), 'sent')
        self.assertIn('Timezone: Europe/Minsk', ai.generate_ai_response.await_args.kwargs['custom_prompt'])


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite(loader.loadTestsFromTestCase(RuntimeClockTests))
    for name in ReplyClockTests.__dict__:
        if name.startswith('test_'):
            suite.addTest(ReplyClockTests(name))
    return suite


if __name__ == '__main__':
    unittest.main(verbosity=2)
