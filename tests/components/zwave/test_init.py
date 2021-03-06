"""Tests for the Z-Wave init."""
import asyncio
from collections import OrderedDict

from homeassistant.bootstrap import async_setup_component
from homeassistant.const import ATTR_ENTITY_ID, EVENT_HOMEASSISTANT_START
from homeassistant.components import zwave
from homeassistant.components.binary_sensor.zwave import get_device
from homeassistant.components.zwave import (
    const, CONFIG_SCHEMA, CONF_DEVICE_CONFIG_GLOB)
from homeassistant.setup import setup_component

import pytest
import unittest
from unittest.mock import patch, MagicMock

from tests.common import get_test_home_assistant
from tests.mock.zwave import MockNetwork, MockNode, MockValue, MockEntityValues


@asyncio.coroutine
def test_missing_openzwave(hass):
    """Test that missing openzwave lib stops setup."""
    result = yield from async_setup_component(hass, 'zwave', {'zwave': {}})
    assert not result


@asyncio.coroutine
def test_valid_device_config(hass, mock_openzwave):
    """Test valid device config."""
    device_config = {
        'light.kitchen': {
            'ignored': 'true'
        }
    }
    result = yield from async_setup_component(hass, 'zwave', {
        'zwave': {
            'device_config': device_config
        }})

    assert result


@asyncio.coroutine
def test_invalid_device_config(hass, mock_openzwave):
    """Test invalid device config."""
    device_config = {
        'light.kitchen': {
            'some_ignored': 'true'
        }
    }
    result = yield from async_setup_component(hass, 'zwave', {
        'zwave': {
            'device_config': device_config
        }})

    assert not result


def test_config_access_error():
    """Test threading error accessing config values."""
    node = MagicMock()

    def side_effect():
        raise RuntimeError

    node.values.values.side_effect = side_effect
    result = zwave.get_config_value(node, 1)
    assert result is None


@asyncio.coroutine
@patch.object(zwave, 'NETWORK')
def test_setup_platform(mock_network, hass, mock_openzwave):
    """Test invalid device config."""
    mock_device = MagicMock()
    hass.data[zwave.DATA_ZWAVE_DICT] = {456: mock_device}
    async_add_devices = MagicMock()

    result = yield from zwave.async_setup_platform(
            hass, None, async_add_devices, None)
    assert not result
    assert not async_add_devices.called

    result = yield from zwave.async_setup_platform(
            hass, None, async_add_devices, {const.DISCOVERY_DEVICE: 123})
    assert not result
    assert not async_add_devices.called

    result = yield from zwave.async_setup_platform(
            hass, None, async_add_devices, {const.DISCOVERY_DEVICE: 456})
    assert result
    assert async_add_devices.called
    assert len(async_add_devices.mock_calls) == 1
    assert async_add_devices.mock_calls[0][1][0] == [mock_device]


@asyncio.coroutine
def test_zwave_ready_wait(hass, mock_openzwave):
    """Test that zwave continues after waiting for network ready."""
    # Initialize zwave
    yield from async_setup_component(hass, 'zwave', {'zwave': {}})
    yield from hass.async_block_till_done()

    with patch.object(zwave.time, 'sleep') as mock_sleep:
        with patch.object(zwave, '_LOGGER') as mock_logger:
            zwave.NETWORK.state = MockNetwork.STATE_STARTED
            hass.bus.async_fire(EVENT_HOMEASSISTANT_START)
            yield from hass.async_block_till_done()

            assert mock_sleep.called
            assert len(mock_sleep.mock_calls) == const.NETWORK_READY_WAIT_SECS
            assert mock_logger.warning.called
            assert len(mock_logger.warning.mock_calls) == 1
            assert mock_logger.warning.mock_calls[0][1][1] == \
                const.NETWORK_READY_WAIT_SECS


@asyncio.coroutine
def test_device_entity(hass, mock_openzwave):
    """Test device entity base class."""
    node = MockNode(node_id='10', name='Mock Node')
    value = MockValue(data=False, node=node, instance=2, object_id='11',
                      label='Sensor',
                      command_class=const.COMMAND_CLASS_SENSOR_BINARY)
    power_value = MockValue(data=50.123456, node=node, precision=3,
                            command_class=const.COMMAND_CLASS_METER)
    values = MockEntityValues(primary=value, power=power_value)
    device = zwave.ZWaveDeviceEntity(values, 'zwave')
    device.hass = hass
    device.value_added()
    device.update_properties()
    yield from hass.async_block_till_done()

    assert not device.should_poll
    assert device.unique_id == "ZWAVE-10-11"
    assert device.name == 'Mock Node Sensor'
    assert device.device_state_attributes[zwave.ATTR_POWER] == 50.123


class TestZWaveDeviceEntityValues(unittest.TestCase):
    """Tests for the ZWaveDeviceEntityValues helper."""

    @pytest.fixture(autouse=True)
    def set_mock_openzwave(self, mock_openzwave):
        """Use the mock_openzwave fixture for this class."""
        self.mock_openzwave = mock_openzwave

    def setUp(self):
        """Initialize values for this testcase class."""
        self.hass = get_test_home_assistant()
        self.hass.start()

        setup_component(self.hass, 'zwave', {'zwave': {}})
        self.hass.block_till_done()

        self.node = MockNode()
        self.mock_schema = {
            const.DISC_COMPONENT: 'mock_component',
            const.DISC_VALUES: {
                const.DISC_PRIMARY: {
                    const.DISC_COMMAND_CLASS: ['mock_primary_class'],
                },
                'secondary': {
                    const.DISC_COMMAND_CLASS: ['mock_secondary_class'],
                },
                'optional': {
                    const.DISC_COMMAND_CLASS: ['mock_optional_class'],
                    const.DISC_OPTIONAL: True,
                }}}
        self.primary = MockValue(
            command_class='mock_primary_class', node=self.node)
        self.secondary = MockValue(
            command_class='mock_secondary_class', node=self.node)
        self.duplicate_secondary = MockValue(
            command_class='mock_secondary_class', node=self.node)
        self.optional = MockValue(
            command_class='mock_optional_class', node=self.node)
        self.no_match_value = MockValue(
            command_class='mock_bad_class', node=self.node)

        self.entity_id = '{}.{}'.format('mock_component',
                                        zwave.object_id(self.primary))
        self.zwave_config = {}
        self.device_config = {self.entity_id: {}}

    def tearDown(self):  # pylint: disable=invalid-name
        """Stop everything that was started."""
        self.hass.stop()

    @patch.object(zwave, 'get_platform')
    @patch.object(zwave, 'discovery')
    def test_entity_discovery(self, discovery, get_platform):
        """Test the creation of a new entity."""
        values = zwave.ZWaveDeviceEntityValues(
            hass=self.hass,
            schema=self.mock_schema,
            primary_value=self.primary,
            zwave_config=self.zwave_config,
            device_config=self.device_config,
        )

        assert values.primary is self.primary
        assert len(list(values)) == 3
        self.assertEqual(sorted(list(values),
                                key=lambda a: id(a)),
                         sorted([self.primary, None, None],
                                key=lambda a: id(a)))
        assert not discovery.async_load_platform.called

        values.check_value(self.secondary)
        self.hass.block_till_done()

        assert values.secondary is self.secondary
        assert len(list(values)) == 3
        self.assertEqual(sorted(list(values),
                                key=lambda a: id(a)),
                         sorted([self.primary, self.secondary, None],
                                key=lambda a: id(a)))

        assert discovery.async_load_platform.called
        # Second call is to async yield from
        assert len(discovery.async_load_platform.mock_calls) == 2
        args = discovery.async_load_platform.mock_calls[0][1]
        assert args[0] == self.hass
        assert args[1] == 'mock_component'
        assert args[2] == 'zwave'
        assert args[3] == {const.DISCOVERY_DEVICE: id(values)}
        assert args[4] == self.zwave_config

        discovery.async_load_platform.reset_mock()
        values.check_value(self.optional)
        values.check_value(self.duplicate_secondary)
        values.check_value(self.no_match_value)
        self.hass.block_till_done()

        assert values.optional is self.optional
        assert len(list(values)) == 3
        self.assertEqual(sorted(list(values),
                                key=lambda a: id(a)),
                         sorted([self.primary, self.secondary, self.optional],
                                key=lambda a: id(a)))
        assert not discovery.async_load_platform.called

        assert values._entity.value_added.called
        assert len(values._entity.value_added.mock_calls) == 1
        assert values._entity.value_changed.called
        assert len(values._entity.value_changed.mock_calls) == 1

    @patch.object(zwave, 'get_platform')
    @patch.object(zwave, 'discovery')
    def test_entity_existing_values(self, discovery, get_platform):
        """Test the loading of already discovered values."""
        self.node.values = {
            self.primary.value_id: self.primary,
            self.secondary.value_id: self.secondary,
            self.optional.value_id: self.optional,
            self.no_match_value.value_id: self.no_match_value,
        }

        values = zwave.ZWaveDeviceEntityValues(
            hass=self.hass,
            schema=self.mock_schema,
            primary_value=self.primary,
            zwave_config=self.zwave_config,
            device_config=self.device_config,
        )
        self.hass.block_till_done()

        assert values.primary is self.primary
        assert values.secondary is self.secondary
        assert values.optional is self.optional
        assert len(list(values)) == 3
        self.assertEqual(sorted(list(values),
                                key=lambda a: id(a)),
                         sorted([self.primary, self.secondary, self.optional],
                                key=lambda a: id(a)))

        assert discovery.async_load_platform.called
        # Second call is to async yield from
        assert len(discovery.async_load_platform.mock_calls) == 2
        args = discovery.async_load_platform.mock_calls[0][1]
        assert args[0] == self.hass
        assert args[1] == 'mock_component'
        assert args[2] == 'zwave'
        assert args[3] == {const.DISCOVERY_DEVICE: id(values)}
        assert args[4] == self.zwave_config
        assert not self.primary.enable_poll.called
        assert self.primary.disable_poll.called

    @patch.object(zwave, 'get_platform')
    @patch.object(zwave, 'discovery')
    def test_node_schema_mismatch(self, discovery, get_platform):
        """Test node schema mismatch."""
        self.node.generic = 'no_match'
        self.node.values = {
            self.primary.value_id: self.primary,
            self.secondary.value_id: self.secondary,
        }
        self.mock_schema[const.DISC_GENERIC_DEVICE_CLASS] = ['generic_match']
        values = zwave.ZWaveDeviceEntityValues(
            hass=self.hass,
            schema=self.mock_schema,
            primary_value=self.primary,
            zwave_config=self.zwave_config,
            device_config=self.device_config,
        )
        values._check_entity_ready()
        self.hass.block_till_done()

        assert not discovery.async_load_platform.called

    @patch.object(zwave, 'get_platform')
    @patch.object(zwave, 'discovery')
    def test_entity_workaround_component(self, discovery, get_platform):
        """Test ignore workaround."""
        self.node.manufacturer_id = '010f'
        self.node.product_type = '0b00'
        self.primary.command_class = const.COMMAND_CLASS_SENSOR_ALARM
        self.entity_id = '{}.{}'.format('binary_sensor',
                                        zwave.object_id(self.primary))
        self.device_config = {self.entity_id: {}}

        self.mock_schema = {
            const.DISC_COMPONENT: 'mock_component',
            const.DISC_VALUES: {
                const.DISC_PRIMARY: {
                    const.DISC_COMMAND_CLASS: [
                        const.COMMAND_CLASS_SWITCH_BINARY],
                }}}

        values = zwave.ZWaveDeviceEntityValues(
            hass=self.hass,
            schema=self.mock_schema,
            primary_value=self.primary,
            zwave_config=self.zwave_config,
            device_config=self.device_config,
        )
        values._check_entity_ready()
        self.hass.block_till_done()

        assert discovery.async_load_platform.called
        # Second call is to async yield from
        assert len(discovery.async_load_platform.mock_calls) == 2
        args = discovery.async_load_platform.mock_calls[0][1]
        assert args[1] == 'binary_sensor'

    @patch.object(zwave, 'get_platform')
    @patch.object(zwave, 'discovery')
    def test_entity_workaround_ignore(self, discovery, get_platform):
        """Test ignore workaround."""
        self.node.manufacturer_id = '010f'
        self.node.product_type = '0301'
        self.primary.command_class = const.COMMAND_CLASS_SWITCH_BINARY

        self.mock_schema = {
            const.DISC_COMPONENT: 'mock_component',
            const.DISC_VALUES: {
                const.DISC_PRIMARY: {
                    const.DISC_COMMAND_CLASS: [
                        const.COMMAND_CLASS_SWITCH_BINARY],
                }}}

        values = zwave.ZWaveDeviceEntityValues(
            hass=self.hass,
            schema=self.mock_schema,
            primary_value=self.primary,
            zwave_config=self.zwave_config,
            device_config=self.device_config,
        )
        values._check_entity_ready()
        self.hass.block_till_done()

        assert not discovery.async_load_platform.called

    @patch.object(zwave, 'get_platform')
    @patch.object(zwave, 'discovery')
    def test_entity_config_ignore(self, discovery, get_platform):
        """Test ignore config."""
        self.node.values = {
            self.primary.value_id: self.primary,
            self.secondary.value_id: self.secondary,
        }
        self.device_config = {self.entity_id: {
            zwave.CONF_IGNORED: True
        }}
        values = zwave.ZWaveDeviceEntityValues(
            hass=self.hass,
            schema=self.mock_schema,
            primary_value=self.primary,
            zwave_config=self.zwave_config,
            device_config=self.device_config,
        )
        values._check_entity_ready()
        self.hass.block_till_done()

        assert not discovery.async_load_platform.called

    @patch.object(zwave, 'get_platform')
    @patch.object(zwave, 'discovery')
    def test_entity_platform_ignore(self, discovery, get_platform):
        """Test platform ignore device."""
        self.node.values = {
            self.primary.value_id: self.primary,
            self.secondary.value_id: self.secondary,
        }
        platform = MagicMock()
        get_platform.return_value = platform
        platform.get_device.return_value = None
        zwave.ZWaveDeviceEntityValues(
            hass=self.hass,
            schema=self.mock_schema,
            primary_value=self.primary,
            zwave_config=self.zwave_config,
            device_config=self.device_config,
        )
        self.hass.block_till_done()

        assert not discovery.async_load_platform.called

    @patch.object(zwave, 'get_platform')
    @patch.object(zwave, 'discovery')
    def test_config_polling_intensity(self, discovery, get_platform):
        """Test polling intensity."""
        self.node.values = {
            self.primary.value_id: self.primary,
            self.secondary.value_id: self.secondary,
        }
        self.device_config = {self.entity_id: {
            zwave.CONF_POLLING_INTENSITY: 123,
        }}
        values = zwave.ZWaveDeviceEntityValues(
            hass=self.hass,
            schema=self.mock_schema,
            primary_value=self.primary,
            zwave_config=self.zwave_config,
            device_config=self.device_config,
        )
        values._check_entity_ready()
        self.hass.block_till_done()

        assert discovery.async_load_platform.called
        assert self.primary.enable_poll.called
        assert len(self.primary.enable_poll.mock_calls) == 1
        assert self.primary.enable_poll.mock_calls[0][1][0] == 123
        assert not self.primary.disable_poll.called


class TestZwave(unittest.TestCase):
    """Test zwave init."""

    def test_device_config_glob_is_ordered(self):
        """Test that device_config_glob preserves order."""
        conf = CONFIG_SCHEMA(
            {'zwave': {CONF_DEVICE_CONFIG_GLOB: OrderedDict()}})
        self.assertIsInstance(
            conf['zwave'][CONF_DEVICE_CONFIG_GLOB], OrderedDict)


class TestZWaveServices(unittest.TestCase):
    """Tests for zwave services."""

    @pytest.fixture(autouse=True)
    def set_mock_openzwave(self, mock_openzwave):
        """Use the mock_openzwave fixture for this class."""
        self.mock_openzwave = mock_openzwave

    def setUp(self):
        """Initialize values for this testcase class."""
        self.hass = get_test_home_assistant()
        self.hass.start()

        # Initialize zwave
        setup_component(self.hass, 'zwave', {'zwave': {}})
        self.hass.block_till_done()
        zwave.NETWORK.state = MockNetwork.STATE_READY
        self.hass.bus.fire(EVENT_HOMEASSISTANT_START)
        self.hass.block_till_done()

    def tearDown(self):  # pylint: disable=invalid-name
        """Stop everything that was started."""
        self.hass.stop()

    def test_add_node(self):
        """Test zwave add_node service."""
        self.hass.services.call('zwave', 'add_node', {})
        self.hass.block_till_done()

        assert zwave.NETWORK.controller.add_node.called
        assert len(zwave.NETWORK.controller.add_node.mock_calls) == 1
        assert len(zwave.NETWORK.controller.add_node.mock_calls[0][1]) == 0

    def test_add_node_secure(self):
        """Test zwave add_node_secure service."""
        self.hass.services.call('zwave', 'add_node_secure', {})
        self.hass.block_till_done()

        assert zwave.NETWORK.controller.add_node.called
        assert len(zwave.NETWORK.controller.add_node.mock_calls) == 1
        assert zwave.NETWORK.controller.add_node.mock_calls[0][1][0] is True

    def test_remove_node(self):
        """Test zwave remove_node service."""
        self.hass.services.call('zwave', 'remove_node', {})
        self.hass.block_till_done()

        assert zwave.NETWORK.controller.remove_node.called
        assert len(zwave.NETWORK.controller.remove_node.mock_calls) == 1

    def test_cancel_command(self):
        """Test zwave cancel_command service."""
        self.hass.services.call('zwave', 'cancel_command', {})
        self.hass.block_till_done()

        assert zwave.NETWORK.controller.cancel_command.called
        assert len(zwave.NETWORK.controller.cancel_command.mock_calls) == 1

    def test_heal_network(self):
        """Test zwave heal_network service."""
        self.hass.services.call('zwave', 'heal_network', {})
        self.hass.block_till_done()

        assert zwave.NETWORK.heal.called
        assert len(zwave.NETWORK.heal.mock_calls) == 1

    def test_soft_reset(self):
        """Test zwave soft_reset service."""
        self.hass.services.call('zwave', 'soft_reset', {})
        self.hass.block_till_done()

        assert zwave.NETWORK.controller.soft_reset.called
        assert len(zwave.NETWORK.controller.soft_reset.mock_calls) == 1

    def test_test_network(self):
        """Test zwave test_network service."""
        self.hass.services.call('zwave', 'test_network', {})
        self.hass.block_till_done()

        assert zwave.NETWORK.test.called
        assert len(zwave.NETWORK.test.mock_calls) == 1

    def test_stop_network(self):
        """Test zwave stop_network service."""
        with patch.object(self.hass.bus, 'fire') as mock_fire:
            self.hass.services.call('zwave', 'stop_network', {})
            self.hass.block_till_done()

            assert zwave.NETWORK.stop.called
            assert len(zwave.NETWORK.stop.mock_calls) == 1
            assert mock_fire.called
            assert len(mock_fire.mock_calls) == 2
            assert mock_fire.mock_calls[0][1][0] == const.EVENT_NETWORK_STOP

    def test_rename_node(self):
        """Test zwave rename_node service."""
        zwave.NETWORK.nodes = {11: MagicMock()}
        self.hass.services.call('zwave', 'rename_node', {
            const.ATTR_NODE_ID: 11,
            const.ATTR_NAME: 'test_name',
        })
        self.hass.block_till_done()

        assert zwave.NETWORK.nodes[11].name == 'test_name'

    def test_remove_failed_node(self):
        """Test zwave remove_failed_node service."""
        self.hass.services.call('zwave', 'remove_failed_node', {
            const.ATTR_NODE_ID: 12,
        })
        self.hass.block_till_done()

        remove_failed_node = zwave.NETWORK.controller.remove_failed_node
        assert remove_failed_node.called
        assert len(remove_failed_node.mock_calls) == 1
        assert remove_failed_node.mock_calls[0][1][0] == 12

    def test_replace_failed_node(self):
        """Test zwave replace_failed_node service."""
        self.hass.services.call('zwave', 'replace_failed_node', {
            const.ATTR_NODE_ID: 13,
        })
        self.hass.block_till_done()

        replace_failed_node = zwave.NETWORK.controller.replace_failed_node
        assert replace_failed_node.called
        assert len(replace_failed_node.mock_calls) == 1
        assert replace_failed_node.mock_calls[0][1][0] == 13

    def test_set_config_parameter(self):
        """Test zwave set_config_parameter service."""
        value = MockValue(
            index=12,
            command_class=const.COMMAND_CLASS_CONFIGURATION,
        )
        value_list = MockValue(
            index=13,
            command_class=const.COMMAND_CLASS_CONFIGURATION,
            type=const.TYPE_LIST,
            data_items=['item1', 'item2', 'item3'],
        )
        node = MockNode(node_id=14)
        node.get_values.return_value = {12: value, 13: value_list}
        zwave.NETWORK.nodes = {14: node}

        self.hass.services.call('zwave', 'set_config_parameter', {
            const.ATTR_NODE_ID: 14,
            const.ATTR_CONFIG_PARAMETER: 13,
            const.ATTR_CONFIG_VALUE: 1,
        })
        self.hass.block_till_done()

        assert node.set_config_param.called
        assert len(node.set_config_param.mock_calls) == 1
        assert node.set_config_param.mock_calls[0][1][0] == 13
        assert node.set_config_param.mock_calls[0][1][1] == 1
        assert node.set_config_param.mock_calls[0][1][2] == 2
        node.set_config_param.reset_mock()

        self.hass.services.call('zwave', 'set_config_parameter', {
            const.ATTR_NODE_ID: 14,
            const.ATTR_CONFIG_PARAMETER: 13,
            const.ATTR_CONFIG_VALUE: 7,
        })
        self.hass.block_till_done()

        assert not node.set_config_param.called
        node.set_config_param.reset_mock()

        self.hass.services.call('zwave', 'set_config_parameter', {
            const.ATTR_NODE_ID: 14,
            const.ATTR_CONFIG_PARAMETER: 12,
            const.ATTR_CONFIG_VALUE: 0x01020304,
            const.ATTR_CONFIG_SIZE: 4,
        })
        self.hass.block_till_done()

        assert node.set_config_param.called
        assert len(node.set_config_param.mock_calls) == 1
        assert node.set_config_param.mock_calls[0][1][0] == 12
        assert node.set_config_param.mock_calls[0][1][1] == 0x01020304
        assert node.set_config_param.mock_calls[0][1][2] == 4
        node.set_config_param.reset_mock()

    def test_print_config_parameter(self):
        """Test zwave print_config_parameter service."""
        value1 = MockValue(
            index=12,
            command_class=const.COMMAND_CLASS_CONFIGURATION,
            data=1234,
        )
        value2 = MockValue(
            index=13,
            command_class=const.COMMAND_CLASS_CONFIGURATION,
            data=2345,
        )
        node = MockNode(node_id=14)
        node.values = {12: value1, 13: value2}
        zwave.NETWORK.nodes = {14: node}

        with patch.object(zwave, '_LOGGER') as mock_logger:
            self.hass.services.call('zwave', 'print_config_parameter', {
                const.ATTR_NODE_ID: 14,
                const.ATTR_CONFIG_PARAMETER: 13,
            })
            self.hass.block_till_done()

            assert mock_logger.info.called
            assert len(mock_logger.info.mock_calls) == 1
            assert mock_logger.info.mock_calls[0][1][1] == 13
            assert mock_logger.info.mock_calls[0][1][2] == 14
            assert mock_logger.info.mock_calls[0][1][3] == 2345

    def test_print_node(self):
        """Test zwave print_config_parameter service."""
        node1 = MockNode(node_id=14)
        node2 = MockNode(node_id=15)
        zwave.NETWORK.nodes = {14: node1, 15: node2}

        with patch.object(zwave, 'pprint') as mock_pprint:
            self.hass.services.call('zwave', 'print_node', {
                const.ATTR_NODE_ID: 15,
            })
            self.hass.block_till_done()

            assert mock_pprint.called
            assert len(mock_pprint.mock_calls) == 1
            assert mock_pprint.mock_calls[0][1][0]['node_id'] == 15

    def test_set_wakeup(self):
        """Test zwave set_wakeup service."""
        value = MockValue(
            index=12,
            command_class=const.COMMAND_CLASS_WAKE_UP,
        )
        node = MockNode(node_id=14)
        node.values = {12: value}
        node.get_values.return_value = node.values
        zwave.NETWORK.nodes = {14: node}

        self.hass.services.call('zwave', 'set_wakeup', {
            const.ATTR_NODE_ID: 14,
            const.ATTR_CONFIG_VALUE: 15,
        })
        self.hass.block_till_done()

        assert value.data == 15

        node.can_wake_up_value = False
        self.hass.services.call('zwave', 'set_wakeup', {
            const.ATTR_NODE_ID: 14,
            const.ATTR_CONFIG_VALUE: 20,
        })
        self.hass.block_till_done()

        assert value.data == 15

    def test_add_association(self):
        """Test zwave change_association service."""
        ZWaveGroup = self.mock_openzwave.group.ZWaveGroup
        group = MagicMock()
        ZWaveGroup.return_value = group

        value = MockValue(
            index=12,
            command_class=const.COMMAND_CLASS_WAKE_UP,
        )
        node = MockNode(node_id=14)
        node.values = {12: value}
        node.get_values.return_value = node.values
        zwave.NETWORK.nodes = {14: node}

        self.hass.services.call('zwave', 'change_association', {
            const.ATTR_ASSOCIATION: 'add',
            const.ATTR_NODE_ID: 14,
            const.ATTR_TARGET_NODE_ID: 24,
            const.ATTR_GROUP: 3,
            const.ATTR_INSTANCE: 5,
        })
        self.hass.block_till_done()

        assert ZWaveGroup.called
        assert len(ZWaveGroup.mock_calls) == 2
        assert ZWaveGroup.mock_calls[0][1][0] == 3
        assert ZWaveGroup.mock_calls[0][1][2] == 14
        assert group.add_association.called
        assert len(group.add_association.mock_calls) == 1
        assert group.add_association.mock_calls[0][1][0] == 24
        assert group.add_association.mock_calls[0][1][1] == 5

    def test_remove_association(self):
        """Test zwave change_association service."""
        ZWaveGroup = self.mock_openzwave.group.ZWaveGroup
        group = MagicMock()
        ZWaveGroup.return_value = group

        value = MockValue(
            index=12,
            command_class=const.COMMAND_CLASS_WAKE_UP,
        )
        node = MockNode(node_id=14)
        node.values = {12: value}
        node.get_values.return_value = node.values
        zwave.NETWORK.nodes = {14: node}

        self.hass.services.call('zwave', 'change_association', {
            const.ATTR_ASSOCIATION: 'remove',
            const.ATTR_NODE_ID: 14,
            const.ATTR_TARGET_NODE_ID: 24,
            const.ATTR_GROUP: 3,
            const.ATTR_INSTANCE: 5,
        })
        self.hass.block_till_done()

        assert ZWaveGroup.called
        assert len(ZWaveGroup.mock_calls) == 2
        assert ZWaveGroup.mock_calls[0][1][0] == 3
        assert ZWaveGroup.mock_calls[0][1][2] == 14
        assert group.remove_association.called
        assert len(group.remove_association.mock_calls) == 1
        assert group.remove_association.mock_calls[0][1][0] == 24
        assert group.remove_association.mock_calls[0][1][1] == 5

    def test_refresh_entity(self):
        """Test zwave refresh_entity service."""
        node = MockNode()
        value = MockValue(data=False, node=node,
                          command_class=const.COMMAND_CLASS_SENSOR_BINARY)
        power_value = MockValue(data=50, node=node,
                                command_class=const.COMMAND_CLASS_METER)
        values = MockEntityValues(primary=value, power=power_value)
        device = get_device(node=node, values=values, node_config={})
        device.hass = self.hass
        device.entity_id = 'binary_sensor.mock_entity_id'
        self.hass.add_job(device.async_added_to_hass())
        self.hass.block_till_done()

        self.hass.services.call('zwave', 'refresh_entity', {
            ATTR_ENTITY_ID: 'binary_sensor.mock_entity_id',
        })
        self.hass.block_till_done()

        assert node.refresh_value.called
        assert len(node.refresh_value.mock_calls) == 2
        self.assertEqual(sorted([node.refresh_value.mock_calls[0][1][0],
                                 node.refresh_value.mock_calls[1][1][0]]),
                         sorted([value.value_id, power_value.value_id]))

    def test_refresh_node(self):
        """Test zwave refresh_node service."""
        node = MockNode(node_id=14)
        zwave.NETWORK.nodes = {14: node}
        self.hass.services.call('zwave', 'refresh_node', {
            const.ATTR_NODE_ID: 14,
        })
        self.hass.block_till_done()

        assert node.refresh_info.called
        assert len(node.refresh_info.mock_calls) == 1
