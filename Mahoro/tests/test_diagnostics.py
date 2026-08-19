"""The diagnostics engine.

These checks run real commands against the host, so the tests cover the parsing
and the guardrails rather than asserting on live network conditions, plus a small
number of loopback checks that are deterministic anywhere.
"""
import pytest

from ping_monitor import diagnostics as dx


# ---------------------------------------------------------------- guardrails


def test_only_named_operations_can_run() -> None:
    """There is no path from a caller to an arbitrary command line."""
    with pytest.raises(ValueError, match="Unknown check"):
        dx.run('rm -rf /')

    with pytest.raises(ValueError, match="Unknown check"):
        dx.run('ping; whoami')


def test_targets_are_validated_before_reaching_the_shell() -> None:
    for hostile in ("127.0.0.1; whoami", "$(id)", "`id`", "10.0.0.1 && del", "|| ls"):
        with pytest.raises(ValueError):
            dx.run('ping', target=hostile)


def test_operations_requiring_a_target_refuse_without_one() -> None:
    with pytest.raises(ValueError, match="needs a target"):
        dx.run('ping')
    with pytest.raises(ValueError, match="needs a target"):
        dx.run('trace')


def test_port_checks_validate_the_port() -> None:
    with pytest.raises(ValueError, match="needs a port"):
        dx.run('port', target='127.0.0.1')
    for bad in (0, -1, 70000):
        with pytest.raises(ValueError, match="between 1 and 65535"):
            dx.run('port', target='127.0.0.1', port=bad)


def test_operations_without_a_target_do_not_need_one() -> None:
    assert dx.OPERATIONS['arp'].needs_target is False
    assert dx.OPERATIONS['routes'].needs_target is False


def test_catalogue_describes_every_operation() -> None:
    entries = dx.catalogue()
    assert {e['key'] for e in entries} == set(dx.OPERATIONS)
    for entry in entries:
        assert entry['label'] and entry['description']
        assert isinstance(entry['needs_target'], bool)
        assert isinstance(entry['needs_port'], bool)


# ---------------------------------------------------------------- parsing


def test_trace_output_is_parsed_into_hops() -> None:
    output = (
        "Tracing route to 10.4.0.9 over a maximum of 20 hops\r\n\r\n"
        "  1     1 ms     1 ms     1 ms  192.168.1.1\r\n"
        "  2     8 ms     9 ms     8 ms  10.0.0.1\r\n"
        "  3     *        *        *     Request timed out.\r\n"
        "  4     *        *        *     Request timed out.\r\n"
    )
    hops = dx._parse_trace(output)

    assert [h['hop'] for h in hops] == [1, 2, 3, 4]
    assert hops[0]['address'] == '192.168.1.1'
    assert hops[1]['rtt_ms'] == [8.0, 9.0, 8.0]
    assert hops[2]['timed_out'] is True
    assert hops[3]['address'] is None


def test_trace_hop_numbers_are_not_mistaken_for_addresses() -> None:
    hops = dx._parse_trace("  7     *        *        *     Request timed out.\r\n")
    assert hops[0]['hop'] == 7
    assert hops[0]['address'] is None


def test_trace_names_where_the_path_stops() -> None:
    """The whole reason to run a trace: which hop last answered."""
    output = (
        "  1     1 ms     1 ms     1 ms  192.168.1.1\r\n"
        "  2     8 ms     8 ms     9 ms  10.0.0.1\r\n"
        "  3     *        *        *     Request timed out.\r\n"
    )
    hops = dx._parse_trace(output)
    answered = [h for h in hops if h['address']]
    silent = [h for h in hops if not h['address']]

    assert answered[-1]['address'] == '10.0.0.1'
    assert answered[-1]['hop'] == 2
    assert silent[0]['hop'] == 3


def test_routing_table_reports_a_default_gateway() -> None:
    """Every host running this has a default route, or the field stays empty."""
    result = dx.run('routes')
    assert result.operation == 'routes'
    assert result.command
    gateway = result.detail.get('default_gateway')
    assert gateway is None or gateway.count('.') == 3 or ':' in gateway


# ---------------------------------------------------------------- loopback


def test_ping_to_loopback_succeeds() -> None:
    result = dx.run('ping', target='127.0.0.1')

    assert result.ok is True
    assert result.detail['replies'] > 0
    assert result.detail['packet_loss'] < 100
    # The command is always reported so an engineer can reproduce it by hand.
    assert '127.0.0.1' in result.command


def test_closed_port_on_loopback_is_reported_as_shut() -> None:
    # Port 9 (discard) is not served on a normal desktop.
    result = dx.run('port', target='127.0.0.1', port=9)

    assert result.ok is False
    assert result.detail == {'port': 9, 'open': False}


def test_unresolvable_name_fails_cleanly() -> None:
    result = dx.run('dns', target='no-such-host.invalid')

    assert result.ok is False
    assert result.detail['addresses'] == []
    assert 'not' in result.summary.lower() or 'does not' in result.summary.lower()


def test_every_result_carries_the_command_it_ran() -> None:
    for operation, kwargs in (
        ('ping', {'target': '127.0.0.1'}),
        ('dns', {'target': 'localhost'}),
        ('arp', {}),
        ('routes', {}),
    ):
        result = dx.run(operation, **kwargs)
        assert result.command, f'{operation} reported no command'
        assert result.label
        assert isinstance(result.as_dict(), dict)
