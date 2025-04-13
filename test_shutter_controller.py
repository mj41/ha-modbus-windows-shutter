import unittest
from unittest.mock import MagicMock, patch, call, ANY
import time
from typing import List, Union, Tuple
import logging  # Added import

# Assuming custom_windows_shutter.py is in the same directory or accessible via PYTHONPATH
from custom_windows_shutter import ShutterController, ModbusRelayError

# Configure basic logging for tests to show logger name and level
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(name)s][%(levelname)s] %(message)s")

# Sample configurations for testing
SAMPLE_MODBUS_CONFIG = {'CONNECTION_TYPE': 'serial', 'DEVICE_PORT': '/dev/null', 'SLAVE_ID': 1}
SAMPLE_SHUTTERS_RAW = {
    'shutter1': {
        'up': {'relay_seq': [{'relay_num': 1, 'delay': 1.0}]},
        'down': {'relay_seq': [{'relay_num': 2, 'delay': 2.0}]}
    },
    'shutter2': {
        'up': {'relay_seq': [{'relay_num': 3, 'delay': 0.5}, {'relay_num': 4, 'delay': 0.7}]},
        'down': {'relay_seq': []}
    },
    'shutter3': {
        'down': {'relay_seq': [{'relay_num': 5, 'delay': 2.0}]}
    }
}
SAMPLE_SHUTTERS = {}
for s_name, actions in SAMPLE_SHUTTERS_RAW.items():
    SAMPLE_SHUTTERS[s_name] = {}
    for a_name, config in actions.items():
        SAMPLE_SHUTTERS[s_name][a_name] = {}
        if 'relay_seq' in config:
            SAMPLE_SHUTTERS[s_name][a_name]['relay_seq'] = []
            if config['relay_seq']:
                for step in config['relay_seq']:
                    new_step = step.copy()
                    new_step['delay_ms'] = int(step['delay'] * 1000)
                    SAMPLE_SHUTTERS[s_name][a_name]['relay_seq'].append(new_step)
            else:
                SAMPLE_SHUTTERS[s_name][a_name]['relay_seq'] = []

SAMPLE_GROUPS = {
    'group1': ['shutter1', 'shutter2'],
    'group2': ['shutter1', 'shutter3'],
    'empty_group': [],
    'missing_shutter_group': ['shutter1', 'nonexistent']
}

EXPECTED_TIMELINES = {
    'shutter1-up': {
        'target': ['shutter1'],
        'action': 'up',
        'timeline': [
            ('on', [1]),
            ('delay', 1000),
            ('on', []),
        ]
    },
    'shutter2-up': {
        'target': ['shutter2'],
        'action': 'up',
        'timeline': [
            ('on', [3]),
            ('delay', 500),
            ('on', [4]),
            ('delay', 700),
            ('on', []),
        ]
    },
    'group1-up': {
        'target': ['shutter1', 'shutter2'],
        'action': 'up',
        'timeline': [
            ('on', [1, 3]),
            ('delay', 500),
            ('on', [1, 4]),
            ('delay', 500),
            ('on', [4]),
            ('delay', 200),
            ('on', []),
        ]
    },
    'group2-down': {
        'target': ['shutter1', 'shutter3'],
        'action': 'down',
        'timeline': [
            ('on', [2, 5]),
            ('delay', 2000),
            ('on', []),
        ]
    },
    'empty_group-up': {
        'target': [],
        'action': 'up',
        'timeline': []
    },
    'missing_action-up': {
        'target': ['shutter1', 'shutter3'],
        'action': 'up',
        'timeline': [
            ('on', [1]),
            ('delay', 1000),
            ('on', []),
        ]
    },
    'empty_sequence-down': {
        'target': ['shutter1', 'shutter2'],
        'action': 'down',
        'timeline': [
            ('on', [2]),
            ('delay', 2000),
            ('on', []),
        ]
    }
}

def compare_timelines(test_case, generated, expected):
    test_case.assertEqual(len(generated), len(expected), "Timeline lengths differ")
    for i, (gen_evt, exp_evt) in enumerate(zip(generated, expected)):
        gen_cmd, gen_data = gen_evt
        exp_cmd, exp_data = exp_evt
        test_case.assertEqual(gen_cmd, exp_cmd, f"Command mismatch at index {i}")
        if gen_cmd == 'delay':
            test_case.assertEqual(gen_data, exp_data, f"Delay mismatch at index {i}")
        elif gen_cmd == 'on':
            test_case.assertListEqual(sorted(gen_data), sorted(exp_data), msg=f"'On' list mismatch at index {i}")
        else:
            test_case.fail(f"Unknown command '{gen_cmd}' in generated timeline at index {i}")


class TestShutterControllerTimeline(unittest.TestCase):

    def setUp(self):
        """Set up for test methods."""
        # Create a logger specific to this test class
        self.logger = logging.getLogger(self.__class__.__name__)
        self.mock_modbus_client_patcher = patch('custom_windows_shutter.ModbusRelayClient')
        self.MockModbusRelayClientClass = self.mock_modbus_client_patcher.start()
        self.mock_client_instance = self.MockModbusRelayClientClass.return_value
        self.mock_client_instance.connect.return_value = True
        self.mock_client_instance.reset_relays.return_value = True
        self.mock_client_instance.write_relay.return_value = True
        self.mock_client_instance.write_relays.return_value = True
        self.mock_client_instance.read_relay_states.return_value = [False] * 32
        self.mock_client_instance.client = MagicMock()
        self.mock_client_instance.client.is_socket_open.return_value = True

        self.controller = ShutterController(SAMPLE_MODBUS_CONFIG, SAMPLE_SHUTTERS, SAMPLE_GROUPS)
        self.controller.client = self.mock_client_instance

    def tearDown(self):
        """Tear down test methods."""
        self.mock_modbus_client_patcher.stop()

    def test_timeline_shutter1_up(self):
        name = 'shutter1-up'
        expected_data = EXPECTED_TIMELINES[name]
        self.logger.debug(f"Testing timeline for {name}: {expected_data}")
        generated_timeline = self.controller._generate_group_timeline(expected_data['target'], expected_data['action'])
        compare_timelines(self, generated_timeline, expected_data['timeline'])

    def test_timeline_shutter2_up(self):
        name = 'shutter2-up'
        expected_data = EXPECTED_TIMELINES[name]
        self.logger.debug(f"Testing timeline for {name}: {expected_data}")více
        generated_timeline = self.controller._generate_group_timeline(expected_data['target'], expected_data['action'])
        compare_timelines(self, generated_timeline, expected_data['timeline'])

    def test_timeline_group1_up(self):
        name = 'group1-up'
        expected_data = EXPECTED_TIMELINES[name]
        self.logger.debug(f"Testing timeline for {name}: {expected_data}")
        generated_timeline = self.controller._generate_group_timeline(expected_data['target'], expected_data['action'])
        compare_timelines(self, generated_timeline, expected_data['timeline'])

    def test_timeline_group2_down(self):
        name = 'group2-down'
        expected_data = EXPECTED_TIMELINES[name]
        self.logger.debug(f"Testing timeline for {name}: {expected_data}")
        generated_timeline = self.controller._generate_group_timeline(expected_data['target'], expected_data['action'])
        compare_timelines(self, generated_timeline, expected_data['timeline'])

    def test_timeline_empty_group_up(self):
        name = 'empty_group-up'
        expected_data = EXPECTED_TIMELINES[name]
        self.logger.debug(f"Testing timeline for {name}: {expected_data}")
        generated_timeline = self.controller._generate_group_timeline(expected_data['target'], expected_data['action'])
        compare_timelines(self, generated_timeline, expected_data['timeline'])

    def test_timeline_missing_action_up(self):
        name = 'missing_action-up'
        expected_data = EXPECTED_TIMELINES[name]
        self.logger.debug(f"Testing timeline for {name}: {expected_data}")
        generated_timeline = self.controller._generate_group_timeline(expected_data['target'], expected_data['action'])
        compare_timelines(self, generated_timeline, expected_data['timeline'])

    def test_timeline_empty_sequence_down(self):
        name = 'empty_sequence-down'
        expected_data = EXPECTED_TIMELINES[name]
        self.logger.debug(f"Testing timeline for {name}: {expected_data}")
        generated_timeline = self.controller._generate_group_timeline(expected_data['target'], expected_data['action'])
        compare_timelines(self, generated_timeline, expected_data['timeline'])

    @patch('custom_windows_shutter.sleep', return_value=None)
    def test_execute_timeline_format(self, mock_sleep):
        sample_timeline_name = 'group1-up'
        timeline_to_execute = EXPECTED_TIMELINES[sample_timeline_name]['timeline']

        success = self.controller._execute_timeline(timeline_to_execute)
        self.assertTrue(success)

        expected_modbus_calls = []
        expected_sleep_calls = []

        expected_modbus_calls.append(call.reset_relays())

        for cmd, data in timeline_to_execute:
            if cmd == 'on':
                expected_state = [False] * 32
                for relay_num in data:
                    if 1 <= relay_num <= 32:
                        expected_state[relay_num - 1] = True
                expected_modbus_calls.append(call.write_relays(expected_state))
            elif cmd == 'delay':
                if data > 0:
                    expected_sleep_calls.append(call(data / 1000.0))

        expected_modbus_calls.append(call.reset_relays())

        actual_modbus_calls = [c for c in self.mock_client_instance.mock_calls if c[0] in ('reset_relays', 'write_relays')]

        self.assertEqual(len(mock_sleep.call_args_list), len(expected_sleep_calls), "Number of sleep calls differ")
        for i, actual_call in enumerate(mock_sleep.call_args_list):
            expected_call = expected_sleep_calls[i]
            # Use .args[0] instead of [0][0] for clarity and robustness
            self.assertAlmostEqual(actual_call.args[0], expected_call.args[0], places=5, msg=f"Sleep call argument mismatch at index {i}")

        self.assertEqual(actual_modbus_calls, expected_modbus_calls)

    @patch('custom_windows_shutter.ShutterController._generate_group_timeline')
    @patch('custom_windows_shutter.ShutterController._execute_timeline')
    def test_control_group_calls_timeline_methods(self, mock_execute, mock_generate):
        group_name = 'group1'
        action = 'up'
        mock_timeline = EXPECTED_TIMELINES[f'{group_name}-{action}']['timeline']
        mock_generate.return_value = mock_timeline
        mock_execute.return_value = True

        success = self.controller.control_group(group_name, action)

        self.assertTrue(success)
        mock_generate.assert_called_once_with(SAMPLE_GROUPS[group_name], action)
        mock_execute.assert_called_once_with(mock_timeline)

    @patch('custom_windows_shutter.ShutterController._generate_group_timeline')
    @patch('custom_windows_shutter.ShutterController._execute_timeline')
    def test_handle_action_calls_group_timeline(self, mock_execute, mock_generate):
        group_name = 'group1'
        action = 'up'
        mock_timeline = EXPECTED_TIMELINES[f'{group_name}-{action}']['timeline']
        mock_generate.return_value = mock_timeline
        mock_execute.return_value = True
        self.controller.check_device_address = MagicMock(return_value=True)

        success = self.controller.handle_action(action, group_name)

        self.assertTrue(success)
        self.controller.check_device_address.assert_called_once()
        mock_generate.assert_called_once_with(SAMPLE_GROUPS[group_name], action)
        mock_execute.assert_called_once_with(mock_timeline)

    @patch('custom_windows_shutter.ShutterController._generate_group_timeline')
    @patch('custom_windows_shutter.ShutterController._execute_timeline')
    def test_handle_action_calls_single_shutter_timeline(self, mock_execute, mock_generate):
        shutter_name = 'shutter1'
        action = 'up'
        mock_timeline = EXPECTED_TIMELINES[f'{shutter_name}-{action}']['timeline']
        mock_generate.return_value = mock_timeline
        mock_execute.return_value = True
        self.controller.check_device_address = MagicMock(return_value=True)

        success = self.controller.handle_action(action, shutter_name)

        self.assertTrue(success)
        self.controller.check_device_address.assert_called_once()
        mock_generate.assert_called_once_with([shutter_name], action)
        mock_execute.assert_called_once_with(mock_timeline)


if __name__ == '__main__':
    unittest.main(argv=['first-arg-is-ignored'], exit=False)
