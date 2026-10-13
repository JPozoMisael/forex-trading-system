"""
Pruebas del bus de eventos sobre Redis Streams (con fakeredis, sin servidor).
"""
import pytest

fakeredis = pytest.importorskip("fakeredis")

from shared.bus import EventBus
from shared.models import Signal, SignalDirection


@pytest.fixture
def bus():
    b = EventBus()
    b._client = fakeredis.FakeRedis(decode_responses=True)
    return b


def consume(bus, channel, group, n, consumer="c1"):
    """Lee hasta n mensajes y corta (simula un servicio que se detiene)."""
    out = []
    gen = bus.subscribe(channel, group=group, consumer=consumer, block_ms=10,
                        should_stop=lambda: len(out) >= n)
    for _, data in gen:
        out.append(data)
        if len(out) >= n:
            gen.close()  # se detiene SIN confirmar el último mensaje
            break
    return out


def test_messages_survive_consumer_downtime(bus):
    # El grupo existe pero el consumidor está "caído" cuando se publica
    consume(bus, "signals:new", "risk-manager", 0)
    sig = Signal(pair="EUR_USD", strategy_name="t", direction=SignalDirection.BUY, entry_price=1.1)
    bus.publish("signals:new", sig)
    received = consume(bus, "signals:new", "risk-manager", 1)
    assert received[0]["pair"] == "EUR_USD" and received[0]["direction"] == "buy"


def test_each_group_gets_every_message(bus):
    consume(bus, "risk:alerts", "monitoring", 0)
    consume(bus, "risk:alerts", "other", 0)
    bus.publish("risk:alerts", {"reason": "x"})
    assert consume(bus, "risk:alerts", "monitoring", 1) == [{"reason": "x"}]
    assert consume(bus, "risk:alerts", "other", 1) == [{"reason": "x"}]


def test_unacked_message_is_redelivered_after_restart(bus):
    consume(bus, "orders:approved", "execution-engine", 0)
    bus.publish("orders:approved", {"order_id": 1})
    bus.publish("orders:approved", {"order_id": 2})
    # Procesa el 1 y "se cae" mientras procesaba el 2 (sin ack)
    first = consume(bus, "orders:approved", "execution-engine", 2)
    assert [m["order_id"] for m in first] == [1, 2]
    # Al reiniciar, el mismo consumidor recibe primero el pendiente
    again = consume(bus, "orders:approved", "execution-engine", 1)
    assert again == [{"order_id": 2}]


def test_state_keys(bus):
    assert bus.set_json_if_absent("k", 100.0)
    assert not bus.set_json_if_absent("k", 50.0)  # no pisa el valor del inicio del día
    assert bus.get_json("k") == 100.0
